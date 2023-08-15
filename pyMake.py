#!/usr/bin/env python3
#
import os
import shutil
import sys
import argparse
import lxml
from   lxml import etree
from enum import IntEnum
from copy import deepcopy
import re

printIntermediateXml: bool = False

"""
Revision History.

1.0.0       17-Jan-2023     RCW

            Initial version.

1.0.1       22-Mar-2023     RCW

            Now always use the compiler path if supplied.

1.0.2       24-Mar-2023     RCW

            Added strip() to clear white space around element text.

1.0.3       11-Apr-2023     RCW

            Added <isys> tags for generating "-isystem path" compiler
            parameters. Specifies path for system includes.

            Parse <dict> entries twice; once for top level, then
            again if <include> files present.

1.0.4       6-Jun-2023      RCW

            Repetitively analyze variable substitution entries to resolve
            backward and forward references.

            Can add <file> entries after a wildcard <file> entry to selectively
            modify optimization and debugging.

1.0.5       12-Jul-2023     RCW

            Added checking for artifact extension to determine output type.
            Library artifacts can still be without extension; we then prepend
            'lib' and append '.a' to the artifact name...we assume a static
            library.

1.0.6       22-Jul-2023     RCW

            Added 'if' attribute that can be attached to any tag. If present,
            it's {key} (if used) will be evaluated to True or False. If the
            result is False, the element is removed from the XML file (in memory)
            by renaming the tag to <culled> before finishing the build.

            New process flow:
                pyMake.xml is parsed
                command line -s dictionary values are added
                top level <dict> elements are added
                <include> files are read, parsed, and appended
                <dict> elements from the configuration and toolchain are added
                all <dict> variable substitutions are reconciled
                all elements with 'if' attributes that evaluate to False are renamed.
                make proceeds

1.0.7       29-Jul-2023     RCW

            Can supply "-i dictfile.xml" on command line to supply <dict> values that
            are evaluated before the body of the XML configuration is evaluated.

            We pass 'varSubDict' to prebuilds.

1.0.8       10-Aug-2023     RCW

            Added '==' and '!=' logic to the 'if' attribute.
            Added the ability to use logical groupings in 'if' attribute using '()'.
            if="({key1};or;{key2}==value2});and;{key3}"

            Added <pre_op> and <post_op> elements for executing scripts or apps
            before and after the build.
"""
REVISION:str = '1.0.8'

# If true, <objects> and <prebuilds> are read from within <configuration>,
# otherwise at the <project> (root) level.
objectsInConfig:bool = False
prebuildInConfig:bool = False
assemblyUsesCpp:bool = True

# Global variable substitution dictionary.
varSubDict:dict = {}

# Global error.
# Setting this to any value should terminate the program.
gError:str | None = None

###############################################################
# XML File parsing.
#
def parseFile(filePath):
    if not os.path.isfile(filePath):
        print(f'Unable to open file: {filePath}')
        return None , None
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        tree = etree.parse(filePath , parser)
        root = tree.getroot()
        return tree , root
    except lxml.etree.ParseError:
        print(f'Error parsing file {filePath}')
        return None , None

###########################################################
# Variable substitution.
# Looks for '{key}' strings in text and replaces them with
# the value from the variable substitution dictionary.
# Returns modified text, original text (if no substitution),
# or None if a {key} is not in the dictionary.
#
def getVarSub(match:re.Match)->str:
    key = match.group()[1:-1]
    if key not in varSubDict:
        raise ValueError(f'ERROR: Key {key} not in dictionary')
    return varSubDict[key]

def varSub(expression)->str | None:
    # Must declare here.
    global gError

    # Assume success.
    gError = None
    # Remove leading and trailing white space.
    expression = expression.strip()
    # First see if there's a variable to substitute.
    if not '{' in expression:
        return expression
    try:
        # Note that getVarSub() will be called for each instance
        # of {key} that is encountered in the string.
        retval = re.sub(r'\{.*?\}', getVarSub, expression)
    except ValueError as err:
        gError = err
        return None
    return retval

###############################################################
# Function to find all <dict> child elements and add
# their key:values to the variable substitution dictionary.
# <configuration> or <toolchain> elements can be ignored.
#
def addDicts(varDict:dict , ele:'etree.Element' , config:str | None, toolchain:str | None):
    for child in ele:
        # Ignore comments.
        if 'Comment' in str(child.tag):
            continue
        # Ignore configuration if no match.
        if child.tag == 'configuration':
            if config is None:
                continue
            if child.get('name') != config:
                continue
            addDicts(varDict, child, None, None)
            continue
        # Ignore toolchain if no match.
        if child.tag == 'toolchain':
            if toolchain is None:
                continue
            if child.get('name') != toolchain:
                continue
            addDicts(varDict, child, None, None)
            continue
        # Add if dictionary entry.
        if child.tag == 'dict':
            # Check for 'if'.
            flag = child.get('if')
            if flag is not None:
                flag = varSub(flag)
                # Ignore (for now) if missing {key}.
                if gError is not None:
                    continue
                child.attrib['if'] = flag
                # Mark and continue if flag is False.
                if not checkIfTag(flag):
                    child.tag = 'culled'
                    continue
            key:str = child.get('key')
            if key is None:
                print(f'ERROR: <dict> elmenent has no key')
                continue
            value = child.text
            if value is None:
                print(f'ERROR: <dict> with key {key} has no value')
                continue
            varDict[key] = value
            # Mark as added.
            child.tag = 'added'
            continue

###############################################################
# Check dependencies for change.
# Returns true if a file's timestamp has changed.
#
def checkMtime(build:'Build' , srcFile:'SourceFile')->bool:
    # Assume nothing has changed.
    retval = False
    # Read the mtime file.
    fd = open(f'{build.configuration}/src/{srcFile.baseName}.mtime')
    files = fd.readlines()
    fd.close()
    # For each file.
    for file in files:
        # Trim carriage return.
        file = file.rstrip('\n')
        # Separate timestamp and filename.
        parts = file.split(':')
        # Get current timestamp.
        timestamp:float = os.path.getmtime(parts[1])
        # Convert to string.
        strTime = str(timestamp)
        # It timestamp is different.
        if parts[0] != strTime:
            # Set flag and break.
            retval = True
            break
    # Return with result.
    return retval

###############################################################
# Generate mtime file from dependency file.
#
def makeMtime(build:'Build' , srcFile:'SourceFile'):
    # Read the dependency file.
    path = f'{build.configuration}/src/{srcFile.baseName}.d'
    fd = open(path , 'r')
    data = fd.read()
    fd.close()
    # Split on space character.
    files = data.split(' ')
    # Pop the first 2 lines.
    files.pop(0)
    files.pop(0)
    # Remove lines with '\n'.
    i = 0
    while i < len(files):
        file = files[i]
        if '\n' in file:
            files.pop(i)
        else:
            i += 1
    # Open the mtime file for writing.
    path = f'{build.configuration}/src/{srcFile.baseName}.mtime'
    fd = open(path , 'w')
    # Timestamp for source file.
    timestamp:float = os.path.getmtime(srcFile.path)
    outLine:str = f'{str(timestamp)}:{srcFile.path}\n'
    fd.write(outLine)
    # For each file.
    for file in files:
        # Get it's modification timestamp.
        timestamp = os.path.getmtime(file)
        outLine = f'{str(timestamp)}:{file}\n'
        fd.write(outLine)
    # Close the file.
    fd.close()

###############################################################
# Compiler and linker flags.
# Get cflagsPre/Post and lflagsPre/Post.
#
def addCompilerFlags(cfg:'Config' , eleRoot:'etree.Element'):
    # Get compiler flags for assembly.
    eleList = eleRoot.findall('aflag')
    for flag in eleList:
        if flag.text != None:
            cfg.aflags.append(flag.text)
    # Get common C/C++ compiler flags.
    eleList = eleRoot.findall('ccflag')
    for flag in eleList:
        if flag.text != None:
            cfg.ccflags.append(flag.text)
    # Get C specific compiler flags.
    eleList = eleRoot.findall('cflag')
    for flag in eleList:
        if flag.text != None:
            cfg.cflags.append(flag.text)
    # Get C++ specific compiler flags.
    eleList = eleRoot.findall('cppflag')
    for flag in eleList:
        if flag.text != None:
            cfg.cppflags.append(flag.text)
    # Get linker flags.
    eleList = eleRoot.findall('lflag')
    for flag in eleList:
        if flag.text != None:
            cfg.lflags.append(flag.text)

###############################################################
# Toolchain.
# Only called if 'cfg' has a toolchain specified.
# Checks that compiler is present.
#
def addToolChain(cfg:'Config' , eleRoot:'etree.Element') -> bool:
    # Find matching toolchain.
    eleToolchain = None
    eleList = eleRoot.findall('toolchain')
    for chain in eleList:
        if chain.get('name') == cfg.toolChainName:
            eleToolchain = chain
            break
    # If not found.
    if eleToolchain == None:
        # If 'native', we really don't need a <toolchain>
        if cfg.toolChainName == 'native':
            return True
        # Else missing toolchain.
        print(f'Toolchain {cfg.toolChainName} not found')
        return False

    # Get compiler path (optional).
    ele = eleToolchain.find('compilerPath')
    if ele == None:
        compilerPath = None
    else:
        compilerPath = ele.text
    # Get compiler prefix (optional).
    ele = eleToolchain.find('compilerPrefix')
    if ele == None:
        compilerPrefix = None
    else:
        compilerPrefix = ele.text

    """
    Will now always use the compiler path if it
    is supplied.
    This is done because a toolchain could be installed
    via apt that matches the compiler prefix, in /usr/bin
    for instance. Trouble could insue if the user really
    needs to use the specified toolchain.
    """

    # If a comppiler path is supplied.
    if compilerPath != None:
        # If a compiler prefix is supplied.
        if compilerPrefix != None:
            ccPrefix = f'{compilerPath}/{compilerPrefix}'
        else:
            ccPrefix = f'{compilerPath}/'
    # Else no compiler path; gcc must be in PATH.
    else:
        if compilerPrefix != None:
            ccPrefix = compilerPrefix
        else:
            ccPrefix = ''

    # Verify that the compiler exists.
    result = os.system(f'{ccPrefix}gcc --version 1>/dev/null')
    if result != 0:
        print(f'Compiler {ccPrefix}gcc not present')
        return False
    else:
        print(f'Compiler {ccPrefix}gcc found')

    # Assign path, prefix, and compiler command.
    cfg.compilerPath = compilerPath
    cfg.compilerPrefix = compilerPrefix
    cfg.ccPrefix = ccPrefix

    # Get toolchain compiler flags.
    addCompilerFlags(cfg , eleToolchain)

    # Success
    return True

###############################################################
# Child projects to pre-build.
# If not specified, config file and configuration will have
# the same values as the parent build.
# It will substitute:
#   <configfile>
#   <configuration>
#   <clean>
#   <prebuilds>
#   <sub>
#
class PreBuild:
    def __init__(self , build:'Build' ,  eleProj:'etree.Element'):
        # Assume failure
        self.initialized = False
        # No subs yet.
        self.subs = []
        # Get path to target.
        self.path = eleProj.get('path')
        if self.path == None:
            return

        # Use our config filename if not specified.
        ele = eleProj.find('configfile')
        if ele == None:
            self.configfile = build.configFile
        else:
            self.configfile = ele.text

        # Check that config file exists.
        path = f'{self.path}/{self.configfile}'
        if not os.path.exists(path):
            print(f'Error: prebuild {path} does not exist')
            return

        # Use our configuration if not specified.
        ele = eleProj.find('configuration')
        if ele == None:
            self.configuration = build.configuration
        else:
            self.configuration = ele.text

        # Use our clean if not specified.
        ele = eleProj.find('clean')
        if ele == None:
            self.makeClean = build.makeClean
        else:
            self.makeClean = True if ele.text == '1' else False

        # Use our prebuilds if not specified.
        ele = eleProj.find('prebuilds')
        if ele == None:
            self.prebuilds = build.prebuilds
        else:
            self.prebuilds = True if ele.text == '1' else False

        # Always get top level <dicts>.
        self.subs = build.subs
        # Add any others.
        eleList = eleProj.findall('sub')
        for ele in eleList:
            if ele.text is not None:
                self.subs.append(ele.text)

        # Success
        self.initialized = True

###############################################################
# File types for source files.
#
class FileType(IntEnum):
    AFILE = 0
    CFILE = 1
    CPPFILE = 2
    UNKNOWN = -1

###############################################################
# Source files.
#

# Append a source file.
# If duplicate name, the existing source will be replaced.
def srcAppend(sourcList:list['SourceFile'], newSource:'SourceFile'):
    source:SourceFile
    for i in range(len(sourcList)):
        source = sourcList[i]
        if (source.filename == newSource.filename):
            sourcList[i] = newSource
            return
    sourcList.append(newSource)

class SourceFile:
    def __init__(self , build:'Build'  , eleFile:'etree.Element'):
        # Assume failure
        self.initialized = False
        # Get the file path.
        path = eleFile.get('path')
        # Set path.
        self.path = path
        # Set file type.
        self.type:FileType = FileType.UNKNOWN
        if self.path.endswith('s'):
            self.type = FileType.AFILE
        elif self.path.endswith('c'):
            self.type = FileType.CFILE
        elif self.path.endswith('cpp'):
            self.type = FileType.CPPFILE
        else:
            print(f'Invalid source file extension: {self.path}')
            return
        # Keep file name for comparison.
        self.filename = os.path.basename(path)
        # Set base name.
        parts = self.filename.split('.')
        self.baseName = parts[0]
        # Confirm that file exists.
        if not os.path.exists(self.path):
            print(f'Source file {self.path} not found')
            return
        # Compiler flag overrides.
        ele = eleFile.find('optimization')
        if ele != None:
            self.optimization = ele.text
        else:
            self.optimization = None
        ele = eleFile.find('debugging')
        if ele != None:
            self.debugging = ele.text
        else:
            self.debugging = None
        # Modification timestamp for dependency tracking.
        self.timestamp = str(os.path.getmtime(self.path))
        # Success.
        self.initialized = True

###############################################################
# Here to find the toolchain specified by the <toolchain>
# element in the specified <configuration>
#
def GetToolchainName(eleRoot:'etree.Element', config:str):
    # Get all the available configurations.
    eleList = eleRoot.findall('configuration')
    # Assume we don't find it.
    result = False
    for eleCfg in eleList:
        cfgName = eleCfg.get('name')
        if cfgName == config:
            result = True
            break
    # Check result.
    if not result:
        print(f'Project configuration {config} not found')
        return None
    # Get the toolchain name.
    toolChainName = eleCfg.find('toolchain').text
    if toolChainName == None:
        print(f'<toolchain> element missing in <configuration>{config}')
        return None
    # Found it.
    return toolChainName

###############################################################
# Configuration class.
# Has all the data needed for compile/link.
#
class Config:
    def __init__(self , build:'Build'  , eleRoot:'etree.Element'):

        # Assume failure.
        self.initialized = False

        #######################################################
        # Get the artifact and type.
        #######################################################

        txt = eleRoot.get('artifact')
        if txt is None:
            print('Project artifact attribute not found')
            return
        parts = txt.split('.')
        self.artifact = parts[0]
        # If extension supplied.
        if len(parts) == 2:
            self.extension = parts[1]
            self.artifactFullName = f'{self.artifact}.{self.extension}'
        # Else no extension.
        else:
            # If extension explicitly defined.
            eleExt = eleRoot.find('extension')
            if eleExt is not None:
                self.extension = eleExt.text
                self.artifactFullName = f'{self.artifact}.{self.extension}'
            else:
                self.extension = None
                self.artifactFullName = self.artifact
        txt = eleRoot.get('type')
        if txt is None:
            print('Project type attribute not found')
            return
        self.library = True if txt == 'library' else False
        # If a library artifact has no extension, we will assume
        # that the user wants a static library with the 'lib'
        # prefix and the '.a' extension.
        # A library artifact that has an extension will not
        # be modified.
        if self.library and self.extension is None:
            if not self.artifact.startswith('lib'):
                self.artifact = f'lib{self.artifact}'
            self.extension = 'a'
            self.artifactFullName = f'{self.artifact}.{self.extension}'

        #######################################################
        # Check that configuration exists.
        #######################################################

        # Get all the available configurations.
        eleList = eleRoot.findall('configuration')
        # Assume we don't find it.
        result = False
        for eleCfg in eleList:
            cfgName = eleCfg.get('name')
            if cfgName == build.configuration:
                # Save configuration ele for compiler flag check below.
                result = True
                break
        # Check result.
        if not result:
            print(f'Project configuration {build.configuration} not found')
            return

        #######################################################
        # Set default configuration data.
        #######################################################

        # Default configuration data.
        self.toolChainName = 'native'
        self.compilerPath = None
        self.compilerPrefix = None
        self.ccPrefix = ''
        self.optimization = '-O0'
        self.debugging = '-g3'
        # Compiler flags for assembly.
        self.aflags = []
        # Common compiler flags for c/c++.
        self.ccflags = []
        # C specific compiler flags.
        self.cflags = []
        # C++ specific compiler flags.
        self.cppflags = []
        # Linker defines.
        self.lflags = []
        # Includes
        self.includes = []
        # Libraries.
        self.objects = []
        # Source files.
        self.sources = []
        # Projects to pre-build.
        self.prebuild = []

        #######################################################
        # Get configuration specific optimization and debug
        # level.
        #######################################################

        ele = eleCfg.find('optimization')
        if ele != None:
            ele.text = ele.text.strip()
            self.optimization = ele.text
        ele = eleCfg.find('debugging')
        if ele != None:
            ele.text = ele.text.strip()
            self.debugging = ele.text
        else:
            self.debugging = None

        #######################################################
        # Get configuration specific compiler flags.
        #######################################################

        addCompilerFlags(self, eleCfg)

        #######################################################
        # Toolchain name; data is assigned later.
        #######################################################

        ele = eleCfg.find('toolchain')
        if ele != None:
            # Get toolchain name.
            self.toolChainName = ele.text

        #######################################################
        # Get list of objects for linking.
        #######################################################

        # Get root element of objects.
        if objectsInConfig:
            ele = eleCfg.find('objects')
        else:
            ele = eleRoot.find('objects')
        if ele != None:
            eleList = ele.findall('obj')
            for ele in eleList:
                text:str = ele.text.strip()
                # Allow null tags.
                if text == None:
                    continue
                # Add to list.
                self.objects.append(text)

        #######################################################
        # Get list of projects to build BEFORE anything else.
        #######################################################

        if objectsInConfig:
            ele = eleCfg.find('prebuilds')
        else:
            ele = eleRoot.find('prebuilds')
        if ele != None:
            eleList = ele.findall('project')
            for ele in eleList:
                prebuild = PreBuild(build , ele)
                if not prebuild.initialized:
                    return
                self.prebuild.append(prebuild)

        #######################################################
        # Get include paths.
        #######################################################

        # Get the list of includes.
        ele = eleRoot.find('includes')
        if ele != None:
            eleList = ele.findall('path')
            for ele in eleList:
                # Get element text.
                text = ele.text
                # Add to list.
                self.includes.append(text)
            eleList = ele.findall('isys')
            for ele in eleList:
                # Get element text.
                text = ele.text
                # Add prefix.
                text = f'-isystem {text}'
                # Add to <ccflag> list.
                self.ccflags.append(text)

        #######################################################
        # Get source files.
        # Wildcards are file paths that end in '/*'.
        # All files within the wildcard folder are added to
        # the list of files to compile.
        # The <exclude>filename</exclude> tag can be added to
        # prevent files in the folder from being compiled.
        #######################################################

        # Get the list of source files.
        srcRoot = eleRoot.find('sources')
        srcList = srcRoot.findall('file')
        for fileEle in srcList:
            # Get the path attribute.
            filePath = fileEle.get('path')
            # If path ends with '/*' it's a wildcard spec.
            if filePath.endswith('/*'):
                # Empty list of excluded names.
                excludeNames = []
                # See if any files are excluded.
                excludeList = fileEle.findall('exclude')
                for excludeEle in excludeList:
                    excludeEle.text = excludeEle.text.strip()
                    excludeNames.append(excludeEle.text)
                # Remove the last two characters of the file path.
                pathBase = filePath[:len(filePath) - 2]
                # Get a list of the files.
                wildList = os.listdir(pathBase)
                # For each file in folder.
                for wildName in wildList:
                    # Limit to 'c', 'c++', and 's' files.
                    if (wildName.endswith('.c')) or (wildName.endswith('.cpp')) or (wildName.endswith('.s')):
                        # Ignore if in exclude list.
                        if wildName in excludeNames:
                            continue
                        # Create element for SourceFile.
                        wildEle = etree.Element('file' , path=f'{pathBase}/{wildName}')
                        srcFile = SourceFile(build , wildEle)
                        if not srcFile.initialized:
                            print(f'Error initializing source file {srcFile.path}')
                            return
                        # Append; will be replaced if explicitly modified.
                        self.sources.append(srcFile)
            else:
                srcFile = SourceFile(build , fileEle)
                if not srcFile.initialized:
                    print(f'Error initializing source file {srcFile.path}')
                    return
                # Replace exisiting if duplicate, otherwise just append.
                srcAppend(self.sources, srcFile)
                #self.sources.append(srcFile)
        # Good if we get here.
        self.initialized = True
        return

###############################################################
# pyMake build object.
#

# Variable substitution for one element; both
# attributes and text.
def doVarsub(ele:'etree.Element')->bool:
    attrList = ele.attrib
    for attr in attrList:
        value = varSub(ele.attrib[attr])
        if value is None:
            return False
        ele.attrib[attr] = value
    if ele.text is not None:
        value = varSub(ele.text)
        if value is None:
            return False
        ele.text = value
    return True

# Recursively replace the keys in the element, and all
# it's children.
# Raises an exception if varSub() fails: key not found.
def replaceKeys(ele:'etree.Element')->bool:
    if not doVarsub(ele):
        raise ValueError(gError)
    eleList = ele.getchildren()
    for child in eleList:
        # Ignore comments.
        if 'Comment' in str(child.tag):
            continue
        if child.tag == 'culled' or child.tag == 'added':
            continue
        if not doVarsub(child):
            raise ValueError(gError)
        replaceKeys(child)
    return True

# Here to check if an 'if' attribute value is true or false.
# The 'if' attribute can be:
#   if="value"       True if value != 0 else False
#   if="key==value"  True if key == value else False
#   if="key!=value"  True if key != value else False
def simpleIfCheck(keyVal:str)->bool:
    # If '==' present.
    parts = keyVal.split('==')
    if len(parts) == 2:
        key = parts[0]
        value = parts[1]
        return True if key == value else False
    # If '!=' present.
    parts = keyVal.split('!=')
    if len(parts) == 2:
        key = parts[0]
        value = parts[1]
        return True if key != value else False
    # Just the key.
    return True if parts[0] != '0' else False

###############################################################
# Courtesy of ChatGPT: an expression evaluator that allows
# grouping of logical expressions with '()'.
# 
def complexIfCheck(expression):
    # Replace the custom logical operators and parentheses
    expression = expression.replace(";or;", " or ").replace(";and;", " and ").replace("(", "( ").replace(")", " )")
    
    # Split the expression into tokens
    tokens = expression.split()
    
    # Initialize stacks to hold values and operators
    value_stack = []
    operator_stack = []
    
    for token in tokens:
        if token == "(":
            operator_stack.append(token)
        elif token == ")":
            # Process until matching "(" is found
            while operator_stack and operator_stack[-1] != "(":
                operator = operator_stack.pop()
                right = value_stack.pop()
                left = value_stack.pop()
                value_stack.append(eval(f"{left} {operator} {right}"))
            operator_stack.pop()  # Remove the "(" from the stack
        elif token in ("and", "or"):
            # Process operators with higher precedence
            while operator_stack and operator_stack[-1] != "(" and operator_stack[-1] != "and":
                operator = operator_stack.pop()
                right = value_stack.pop()
                left = value_stack.pop()
                value_stack.append(eval(f"{left} {operator} {right}"))
            operator_stack.append(token)
        else:
            # Variable token
            value_stack.append(simpleIfCheck(token))
    
    # Process any remaining operators
    while operator_stack:
        operator = operator_stack.pop()
        right = value_stack.pop()
        left = value_stack.pop()
        value_stack.append(eval(f"{left} {operator} {right}"))
    
    return value_stack[0]

# Here to check if an 'if' attribute expression is true or false.
# When this function is called, all {key} values in the expression
# will have been replaced by variable substitution.
# A simple expression can be:
#   if="key"         True if key != 0 else False
#   if="key==value"  True if key == value else False
#   if="key!=value"  True if key != value else False
# A complex expression can contain the logical operators:
#   ";and;", and ";or;"
#   if="key1;or;key2"
# A complex expression can also have logical grouping with '()'.
#   if="(key1==value1;or;key2!=value2);and;key3"
# 
def checkIfTag(expression:str)->bool:
    if ';' not in expression:
        return simpleIfCheck(expression)
    return complexIfCheck(expression)

# Recursively rename tags that have an 'if' attribute
# that evaluates to False.
# Renaming these tags effectively removes them
# from the XML configuration file.
def cullTags(element:'etree.Element'):
    # If the element has not already been culled or added.
    if element.tag != 'culled' and element.tag != 'added':
        # And if there's an 'if' attribute.
        if 'if' in element.attrib:
            # Get the entire attribute value.
            keyVal = element.attrib['if']
            # Check keyVal.
            if not checkIfTag(keyVal):
                element.tag = 'culled'
    # Recursively traverse the child elements.
    for child in element:
        if child.tag == 'culled':
            continue
        cullTags(child)

# The Build object holds all the information from
# the command line and the XML configuration file.
# It's properties define everything needed for a build.
# It's methods perform the steps needed for a build.
class Build:
    def __init__(self, 
                 cfgfile:str, 
                 config:str, 
                 clean:bool=False, 
                 prebuilds:bool=False, 
                 subs:'list[str]' = [], 
                 dictFile:str='None', 
                 subDict:dict=None, 
                 singleFile:str='None') -> None:

        found:bool

        # Assume failure.
        self.initialized = False

        # Store working directory.
        self.cwd = os.getcwd()

        # Sanity check.
        if not os.path.exists(cfgfile):
            print('Need configuration xml file')
            return
        # Parse the xml configuration file.
        tree , root = parseFile(cfgfile)
        if tree == None:
            print(f'Error parsing configuration file: {cfgfile}')
            return

        # Assign values from command line.
        self.configFile = cfgfile
        self.configuration = config
        self.makeClean = clean
        self.prebuilds = prebuilds
        self.subs = subs
        if singleFile != 'None':
            if not singleFile.endswith('.s') and not singleFile.endswith('.c') and not singleFile.endswith('.cpp'):
                print(f'Unable to compile {singleFile}: wrong file type, need .s,.c,.cpp')
                return
            # Set clean to force compile.
            self.makeClean = True
        self.singleFile = singleFile

        # Add the configuration to variable substitution.
        # Using a shorter version for brevity if used as {key}.
        varSubDict['config'] = self.configuration

        # Add command line key:value pairs to variable substitution.
        for kvp in subs:
            parts = kvp.split(':')
            if len(parts) != 2:
                print(f'ERROR: Invalid key:value pair {kvp}')
                return
            varSubDict[parts[0]] = parts[1]

        # Apply any operations to be done before we proceed.
        preopList = root.findall('pre_op')
        for preop in preopList:
            cmd = preop.text
            cmd = varSub(cmd)
            if cmd is None:
                print(f'ERROR: Unknown key in <pre_op>: {cmd}')
                return
            result = os.system(cmd)
            if result != 0:
                print(f'ERROR: <pre_op> command failed: {cmd}')
                return
            
        # Save any post operations.
        self.postop = []
        postopList = root.findall('post_op')
        for postop in postopList:
            cmd = postop.text
            cmd = varSub(cmd)
            if cmd is None:
                print(f'ERROR: Unknown key in <post_op>: {cmd}')
            self.postop.append(cmd)

        # Add key:value dictionary if supplied.
        if subDict is not None:
            for sub in subDict:
                varSubDict[sub] = subDict[sub]

        # Add <dict> elements from command line XML file.
        # Root element of this file must have tag name 'dicts'.
        if dictFile != 'None':
            # Sanity check.
            dictFile = dictFile.strip()
            if not os.path.exists(dictFile):
                print(f'ERROR: XML include file not found: {dictFile}')
                return
            # Parse it.
            incTree, incRoot = parseFile(dictFile)
            if incRoot is None:
                print(f'ERROR: Unable to parse XML include file: {dictFile}')
                return
            # Check root tag name.
            if incRoot.tag != 'dicts':
                print(f'ERROR: Root of include file {dictFile} does not have "dicts" tag')
                return
            # Add dictionay entries.
            addDicts(varSubDict, incRoot, None, None)

        # Get top level <dict> entries.
        # These may be used directly below by <include> elements.
        # Now, these <dict> elements may have 'if' attributes with
        # as yet unresolved {key} values. They will be ignored.
        addDicts(varSubDict , root , None , None)

        # Add any <include> files.
        # Appends a deep copy of each element to the root.
        # Using variable substitution here for file names
        # in case an <include> element contains {key}.
        # Any {key} references in <include> files must be fully
        # defined by previouse <dict> entries.
        # If an <include> element has an 'if' qualifier, it must
        # be able to be immediately evaluated.
        #
        # NOTE: <include> files whose root element tag is 'dicts'
        #       will immediately add all the <dict> elements to
        #       varSubDict and will NOT add the elements themselves
        #       to the configuration XML image.
        incList = root.findall('include')
        for inc in incList:
            if 'if' in inc.attrib:
                flag = inc.attrib['if']
                flag = varSub(flag)
                if gError is not None:
                    print(gError)
                    return
                inc.attrib['if'] = flag
                result = checkIfTag(flag)
                if result is None:
                    print(gError)
                    return
                if not result:
                    inc.tag = 'culled'
                    continue
            pathText = inc.text.strip()
            incPath = varSub(pathText)
            if gError is not None:
                print(gError)
                return
            if incPath is None or not os.path.exists(incPath):
                print(f'Include file {pathText} not found')
                return
            incTree,incRoot = parseFile(incPath)
            if incTree == None:
                print(f'Error parsing include file: {cfgfile}')
                return
            # If only <dict> elements, add them directly to the dictionary.
            if incRoot.tag == 'dicts':
                print(f'Adding <dict> elements from {incPath}')
                addDicts(varSubDict, incRoot, None, None)
            # Else include all as part of configuraion.
            else:
                print(f'Adding include file {incPath}')
                # Append include file data.
                for child in incRoot:
                    root.append(deepcopy(child))
            # Mark as added.
            inc.tag = 'added'

        # Add <configuration> <dict> elements from the project root level.
        # Ignore <toolchain> elements.
        # NOTE: This function does NOT do variable substitution.
        addDicts(varSubDict, root, self.configuration, None)

        # Get the <toolchain> name specified in the <configuration>.
        toolChainName = GetToolchainName(root, self.configuration)
        if toolChainName == None:
            return

        # Add <toolchain> <dict> elements from the project root level.
        # Ignore <configuration> elements (already done).
        # NOTE: This function does NOT do variable substitution.
        addDicts(varSubDict , root , None , toolChainName)

        # We are now finished adding raw <dict> entries.
        # <dict> values may themselves have {key} entries.
        # Example:
        #   <dict key="tool">myTool</dict>
        #   <dict key="fooTool">foo/{tool}</dict>
        # We now loop through the <dict> entries to reconcile
        # values that have a {key}.
        # Setting an arbitrary limit of 10 iterations.
        checkLimit:int = 10
        while True:
            all_good = True
            for key in varSubDict:
                value = varSub(varSubDict[key])
                if gError is not None:
                    print(gError)
                    return
                varSubDict[key] = value
                if '{' in value:
                    all_good = False
                    checkLimit -= 1
                    if checkLimit == 0:
                        print(f'ERROR: Cannot reconcile all <dict> values')
                        return
            if all_good:
                break
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme1.xml' , pretty_print=True)

        # We have all the <dict> elments resolved.
        # Now we need to recursively traverse the XML file
        # and replace all the {key} values.
        # It is possible for an undefined {key} to be present
        # in a tag; in this case, an exception will be raised,
        # and we bail.
        try:
            replaceKeys(root)
        except ValueError as err:
            print(err)
            return
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme2.xml' , pretty_print=True)

        # Now, we rename all tags as <culled> that have an 'if'
        # attribute that evaluates to False.
        try:
            cullTags(root)
        except ValueError as err:
            print(err)
            return

        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme3.xml' , pretty_print=True)
        
        # Now we need one more pass to add <dict> entries whose
        # {key} values have just been resolved.
        addDicts(varSubDict , root , None , None)

        # At this point, the XML file is complete with all
        # included files and <dict> values evaluated.
        # We now proceed to process the file.

        #######################################################
        # Assign configuration data.
        # Collects flags, includes, objects, sources, etc.
        #######################################################
        self.cfg = Config(self , root)
        if not self.cfg.initialized:
            return

        # If compiling single file; error if it's in the source list.
        if self.singleFile != 'None':
            found = False
            src:SourceFile
            for src in self.cfg.sources:
                if src.filename == self.singleFile:
                    found = True
                    break
            if not found:
                print(f'Single file {self.singleFile} not in source file list')
                return

        # Set toochain data.
        if not addToolChain(self.cfg , root):
            return

        # Success.
        self.initialized = True

    ###########################################################
    # Create build folders.
    #
    def doFolders(self):
        # If cleaning, create new build path.
        buildPath = f'{self.configuration}/src'
        if self.makeClean:
            if os.path.exists(self.configuration):
                shutil.rmtree(self.configuration)
            os.makedirs(buildPath)
        # Else make sure build path exists.
        else:
            if not os.path.exists(buildPath):
                self.makeClean = True
                os.makedirs(buildPath)

    ###########################################################
    # Build dependent projects.
    #
    def doPrebuilds(self)->int:
        proj:PreBuild
        for proj in self.cfg.prebuild:
            # Where we return.
            cur_dir = os.getcwd()
            # Change to the target folder.
            os.chdir(proj.path)
            # Execute command.
            result = pyMake(proj.configfile, 
                            proj.configuration, 
                            proj.makeClean, 
                            proj.prebuilds, 
                            proj.subs,
                            'None',         # Inc file
                            varSubDict,
                            'None')         # Single file
            # Return.
            os.chdir(cur_dir)
            if result != 0:
                print(f'Unable to pre-build {proj.name}')
                return 1
        # Success.
        return 0

    ###########################################################
    # Compile all the source files.
    # Returns success/failure, and if linking is needed.
    #
    # gcc/g++ command line structure:
    #   $ gcc [options] [source files] [object files] [-o output file]
    #
    def doCompile(self)->'bool,bool':
        # Create include string used for all source files.
        ccmd_inc = ''
        for include in self.cfg.includes:
            ccmd_inc += f' -I{include}'

        # Assume we will not compile ANY source files.
        needLink:bool = False

        # For each source file.
        srcFile:SourceFile
        for srcFile in self.cfg.sources:
            # Only check dependencies if not cleaning.
            if not self.makeClean:
                # Check for mtime file.
                if not os.path.exists(f'{self.configuration}/src/{srcFile.baseName}.mtime'):
                    # Compile is true.
                    compile = True
                # Else
                else:
                    compile = checkMtime(self , srcFile)
                # Continue if compile is false.
                if not compile:
                    continue

            # If compiling at least one file; we will need to link, unless
            # we're just compiling one file (-o command line parameter).
            if self.singleFile == 'None':
                needLink = True
            else:
                # Continue if not file match.
                if srcFile.filename != self.singleFile:
                    continue

            # Create start of compiler string.
            if srcFile.type == FileType.CPPFILE:
                ccmd = f'{self.cfg.ccPrefix}g++'
            elif srcFile.type == FileType.CFILE:
                ccmd = f'{self.cfg.ccPrefix}gcc'
            else:
                if assemblyUsesCpp:
                    ccmd = f'{self.cfg.ccPrefix}g++'
                else:
                    ccmd = f'{self.cfg.ccPrefix}as'

            # Add optimization and debugging options.
            if srcFile.optimization == None:
                ccmd += f' {self.cfg.optimization}'
            else:
                ccmd += f' {srcFile.optimization}'
            if srcFile.debugging == None:
                if self.cfg.debugging != None:
                    ccmd += f' {self.cfg.debugging}'
            else:
                ccmd += f' {srcFile.debugging}'
                
            # All warnings, don't link.
            ccmd += ' -Wall -c'

            # If assembly file.
            if srcFile.type == FileType.AFILE:
                for define in self.cfg.aflags:
                    ccmd += f' {define}'
            # Else C/C++.
            else:
                # Add common C/C++ flags.
                for define in self.cfg.ccflags:
                    ccmd += f' {define}'
                # Add C flags.
                if srcFile.type == FileType.CFILE:
                    for define in self.cfg.cflags:
                        ccmd += f' {define}'
                # Add C++ flags.
                else:
                    for define in self.cfg.cppflags:
                        ccmd += f' {define}'

            # Add the include options.
            ccmd += ccmd_inc

            """
            Add flag to generate the dependency file: 'baseName.d'
            The original flags from the Eclipse build are:
                -MMD -MP -MF"src/cdom.d" -MT"src/cdom.o"
            However, all that is needed (apparently), is the '-MMD'.
            The '-MP' (phony) adds a duplicate listing in the '.d' file.
            Original flags before culling:
                ccmd += f' -MMD MP'
                ccmd += f' -MF{configuration}/src/{srcFile.baseName}.d'
                ccmd += f' -MT{configuration}/src/{srcFile.baseName}.o'
            """
            # Add flag to generate dependencies:
            ccmd += f' -MMD'

            # Add source file.
            ccmd += f' {srcFile.path}'

            # Add output file name: -o src/cdom.o ../src/cdom.c
            ccmd += f' -o {self.configuration}/src/{srcFile.baseName}.o'

            # Execute compiler command and show the work.
            print(f'\nCompiling {srcFile.path}\n')
            print(ccmd)
            result = os.system(ccmd)

            # Return failure if compile error.
            if result != 0:
                return False , False
            # Create mtime file from generated dependency file.
            makeMtime(self , srcFile)

        # Return with link yes/no.
        return True , needLink

    ###########################################################
    # Create the artifact.
    #
    # gcc/g++ command line structure:
    #   $ gcc [flags] [-o output file] [source files] [object files]
    #
    def doArtifact(self)->int:

        ###########################################################
        # If library, link compiled source files and return.
        ###########################################################

        if self.cfg.library:
            if self.cfg.extension == 'dll' or self.cfg.extension == 'so':
                # Use g++; both dll & so are shared.
                arcmd = f'{self.cfg.ccPrefix}g++'
                arcmd += ' -shared'
                # Linker flags
                for flag in self.cfg.lflags:
                    arcmd += f' {flag}'
                arcmd += f' -o {self.configuration}/{self.cfg.artifactFullName}'
            else:
                # Use archive command.
                arcmd  = f'{self.cfg.ccPrefix}ar -r'
                # Full path for library artifact.
                arcmd += f' {self.configuration}/{self.cfg.artifactFullName}'
            # Add compiled source files.
            src:SourceFile
            for src in self.cfg.sources:
                arcmd += f' {self.configuration}/src/{src.baseName}.o'
            # Add any other objets.
            for obj in self.cfg.objects:
                arcmd += f' {obj}'
            # Execute archive command and show the work.
            print(f'\nCreating {self.cfg.artifactFullName}\n')
            print(arcmd)
            result = os.system(arcmd)
            # Return failure if link error.
            return result

        ###########################################################
        # Else if executable, link source files, objects, ldscripts, etc.
        ###########################################################

        else:
            # Create start of linker string.
            linkCmd = f'{self.cfg.ccPrefix}g++'

            # Add linker flag options.
            for ldefine in self.cfg.lflags:
                linkCmd += f' {ldefine}'

            # Add source files.
            for src in self.cfg.sources:
                linkCmd += f' {self.configuration}/src/{src.baseName}.o'

            # If there are objects to link.
            if len(self.cfg.objects) != 0:
                # Add 'start-group'.
                linkCmd += f' -Wl,--start-group'

                # Add objects.
                for obj in self.cfg.objects:
                    linkCmd += f' {obj}'

                # Add end group.
                linkCmd += ' -Wl,--end-group'

            # Output file name.
            if self.cfg.extension == None:
                linkCmd += f' -o {self.configuration}/{self.cfg.artifact}'
            elif self.cfg.extension == 'bin' or self.cfg.extension == 'hex':
                # Create elf version for objcopy below.
                linkCmd += f' -o {self.configuration}/{self.cfg.artifact}.elf'
            else:
                linkCmd += f' -o {self.configuration}/{self.cfg.artifactFullName}'

            # Execute link command and show the work.
            print(f'\nLinking {self.cfg.artifact}\n')
            print(linkCmd)
            result = os.system(linkCmd)
            # Return failure if link error.
            if result != 0:
                return 1

            # Create binary version?
            if self.cfg.extension == 'bin':
                cmd = f'{self.cfg.ccPrefix}objcopy -O binary {self.configuration}/{self.cfg.artifact}.elf {self.configuration}/{self.cfg.artifact}.bin'
                print(f'\nCreating {self.cfg.artifact}.bin\n')
                print(cmd)
                result = os.system(cmd)
                if result != 0:
                    return 1

            # Create hex version?
            if self.cfg.extension == 'hex':
                cmd = f'{self.cfg.ccPrefix}objcopy -O binary {self.configuration}/{self.cfg.artifact}.elf {self.configuration}/{self.cfg.artifact}.hex'
                print(f'\nCreating {self.cfg.artifact}.hex\n')
                print(cmd)
                result = os.system(cmd)
                if result != 0:
                    return 1

            # Success.
            return 0
        
    def doPostOps(self):
        retval = 0
        for postop in self.postop:
            result = os.system(postop)
            if result != 0:
                retval = 1
                print(f'ERROR: <post_op> failed: {postop}')
        return retval

###############################################################
# Entry point for pyMake.
# Creates a PyMakeBuild object and builds the project(s)
#
def pyMake(cfgfile:str, 
           config:str, 
           clean:bool, 
           prebuilds:bool, 
           subs:'list[str]', 
           dictFile:str='None', 
           subDict:dict=None, 
           singleFile:str='None')->int:

    # Where are we?
    print(f'\npyMake executing in {os.getcwd()}')

    # Create the build object.
    try:
        build = Build(cfgfile, 
                      config, 
                      clean, 
                      prebuilds, 
                      subs, 
                      dictFile, 
                      subDict, 
                      singleFile)
        if not build.initialized:
            return 1
    except KeyError as e:
        print(e)
        return 1

    # Create the build folders.
    build.doFolders()

    # Build prerequisite projects if requested.
    if prebuilds:
        if build.doPrebuilds() != 0:
            return 1

    # Types.
    result:bool
    needLink:bool

    # Compile source files.
    result , needLink = build.doCompile()
    if not result:
        return 1
    
    # Return if library and no linking required.
    retval:int = 0
    if (build.cfg.library and not needLink) or (singleFile != 'None'):
        if singleFile == 'None':
            retval = build.doPostOps()
        print(f'\npyMake returning from {os.getcwd()}')
        return retval

    # Build the artifact.
    retval = build.doArtifact()

    if retval == 0:
        # Post operations.
        retval = build.doPostOps()

    # Return message.
    print(f'\npyMake returning from {os.getcwd()}')

    # Return.
    return retval

###############################################################################
# Standalone execution.
#
if __name__ == "__main__":

    parser = argparse.ArgumentParser(
                    prog = 'pyMake.py',
                    description = 'Compiles an application as specified in the configuration XML file',
                    epilog = 'Example: pyMake.py -c -p -g Debug -s target:x86 -o main.c')
    parser.add_argument('-v', '--version',  help='Show version number:                      default=False',action='store_true')
    parser.add_argument('-c', '--clean',    help='Clean before building:                    default=False',action='store_true')
    parser.add_argument('-p', '--prebuild', help='Execute recursive pyMake on prebuilds:    default=False',action='store_true')
    parser.add_argument('-f', '--file',     help='XML Configuration file to use:            default=pyMake.xml',default='pyMake.xml')
    parser.add_argument('-g', '--cfg',      help='Build configuration used in XML file:     default=Release',default='Release')
    parser.add_argument('-o', '--one',      help='Compile just the specified file:          default=None',default='None')
    parser.add_argument('-s', '--sub',      help='Append semicolon delimited key/value pair to variable substitution dictionary',action='append',default=[])
    parser.add_argument('-i', '--inc',      help='Include XML <dict> file:                  default=None',default='None')
                    
    try:
        args = parser.parse_args()

        args.file = args.file.strip()
        args.cfg = args.cfg.strip()
        for i in range(len(args.sub)):
            args.sub[i] = args.sub[i].lstrip()
        args.one = args.one.strip()

        # Return here if just version.
        if args.version:
            print(f'pyMake.py version {REVISION}')
            sys.exit(0)

        # Show the arguments.
        print('')
        print('Build parameters:')
        print(f'    clean:          {args.clean}')
        print(f'    prebuild:       {args.prebuild}')
        print(f'    file:           {args.file}')
        print(f'    cfg:            {args.cfg}')
        print(f'    one:            {args.one}')
        print(f'    sub:            {args.sub}')
        print(f'    inc:            {args.inc}')

        # Execute and back.
        cwd_main = os.getcwd()
        retval_main = pyMake(args.file, 
                             args.cfg, 
                             args.clean, 
                             args.prebuild, 
                             args.sub, 
                             args.inc, 
                             None,          # No dictionary from command line
                             args.one)
        os.chdir(cwd_main)
        print(f'\npyMake exiting with code {retval_main}')
        sys.exit(retval_main)

    except Exception as e:
        print(f'ERROR: pyMake: Exception encountered: {e}')
        sys.exit(1)
