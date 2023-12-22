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
            NOTE: Removed 24-Aug-2023; can be done with standard flags.

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

1.0.9       21-Sep-2023     RCW

            Modified process flow to read in all the included files, cull any
            non-applicable <configuration> and <toolchain> elements, then take
            care of all the variable substitutions.

            Have -x command line parameter that prints intermediate xml results.

            There can be more than one -i included xml file on the command line.
"""
REVISION:str = '1.0.9'

# If true, <objects> and <prebuilds> are read from within <configuration>,
# otherwise at the <project> (root) level.
objectsInConfig:bool = False
prebuildInConfig:bool = False
assemblyUsesCpp:bool = True

# Global variable substitution dictionary.
varSubDict:dict = {}

# Global error.
# Setting this to any value should terminate the program.
# _RCW_: 3.10-->3.8
gError:str = None

###############################################################
# Return the string value of an element.
#
def eleToString(ele):
    return etree.tostring(ele, pretty_print=True).decode('utf-8')

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
    except lxml.etree.ParseError as err:
        print(f'Error parsing file {filePath}:{err}')
        return None , None

###########################################################
# Variable substitution.
# Looks for '{key}' strings in text and replaces them with
# the value from the variable substitution dictionary.
# Returns modified text, original text (if no substitution),
# or None if a {key} is not in the dictionary.
#
def getVarSub(match:re.Match, required:bool=True)->str:
    key = match.group()[1:-1]
    if key not in varSubDict:
        if required:
            raise ValueError(f'ERROR: Key {key} not in dictionary')
        else:
            return '{' + key + '}'
    retval = varSubDict[key]
    # Leave undefined keys alone.
    if retval == '_undefined_':
        retval = '{' + key + '}'
    return retval

# Does the variable substitution.
# Returns 'str' or None if undefined key.
# _RCW_: 3.10-->3.8
def varSub(expression:str, required:bool=True)->str:
    # Must declare here.
    global gError

    # We give undefined values a pass.
    if expression == '_undefined_':
        return expression
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
        retval = re.sub(r'\{.*?\}', lambda match: getVarSub(match, required), expression)
    except ValueError as err:
        if required:
            gError = err
            return None
        else:
            return expression
    return retval

###############################################################
# Function to find all <dict> child elements and add
# their key:values to the variable substitution dictionary.
# Comments, 'added' and 'culled' elements are ignored.
#
# _RCW_: 3.10-->3.8

###############################################################
# Add a single <dict> element.
#
def addDict(varDict:dict, child:'etree.Element', required:bool):
    # Check for 'if'; evaluation.
    result = checkIfElement(child, required)
    # None: evaluation required, but has undefined {key}.
    if result is None:
        eleString = eleToString(child)
        raise ValueError(f'ERROR: Unknown key in <dict>: {eleString}')
        # NOTREACHED
    # False: no 'if', or 'if' evaluation is False.
    if result is False:
        return
    # True: no 'if', or 'if' evaluation is True.
    key: str = child.get('key')
    if key is None:
        print(f'ERROR: <dict> elmenent has no key')
        return
    value = child.text
    if value is None:
        print(f'ERROR: <dict> with key {key} has no value')
        return
    varDict[key] = value
    # Mark as added.
    child.tag = f'{child.tag}-added'

###############################################################
# Recursively find and add <dict> elements.
#   recursive_function(item_with_subItems)
#       process the item_with_subItems
#       for each subItem:
#           recursive_function(subItem)
#
def addDicts(varDict:dict, ele:'etree.Element', required:bool=False):
    # Get tag name.
    tag = str(ele.tag)
    # Ignore these.
    if 'Comment' in tag or 'culled' in tag or 'added' in tag:
        return
    # Process if <dict>.
    if tag == 'dict':
        addDict(varDict, ele, required)
        return
    # Now process any children.
    for child in ele:
        addDicts(varDict, child, required)

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
# Toolchain.
# Only called if 'cfg' has a toolchain specified.
# Checks that compiler is present.
#
def addToolChain(cfg:'Config' , eleToolchain:'etree.Element') -> bool:
    # If native toolchain, just return.
    if eleToolchain.get('name') == 'native':
        return True
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
        print(f'ERROR:Compiler {ccPrefix}gcc not present')
        return False
    else:
        print(f'Compiler {ccPrefix}gcc found')

    # Assign path, prefix, and compiler command.
    cfg.compilerPath = compilerPath
    cfg.compilerPrefix = compilerPrefix
    cfg.ccPrefix = ccPrefix

    # Success
    return True

###############################################################
# Class of compiler and linker flags.
#
class Flags:
    def __init__(self):
        self.a = []
        self.c = []
        self.cc = []
        self.cpp = []
        self.l = []
    
    def addFlags(self, eleRoot:'etree.Element'):
        # Get compiler flags for assembly.
        eleList = eleRoot.findall('aflag')
        for flag in eleList:
            if flag.text != None:
                self.a.append(flag.text)
        # Get C specific compiler flags.
        eleList = eleRoot.findall('cflag')
        for flag in eleList:
            if flag.text != None:
                self.c.append(flag.text)
        # Get common C/C++ compiler flags.
        eleList = eleRoot.findall('ccflag')
        for flag in eleList:
            if flag.text != None:
                self.cc.append(flag.text)
        # Get C++ specific compiler flags.
        eleList = eleRoot.findall('cppflag')
        for flag in eleList:
            if flag.text != None:
                self.cpp.append(flag.text)
        # Get linker flags.
        eleList = eleRoot.findall('lflag')
        for flag in eleList:
            if flag.text != None:
                self.l.append(flag.text)

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
#
def srcAppend(sourcList:"list['SourceFile']", newSource:'SourceFile'):
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
        # File specific compiler flags.
        self.flags = Flags()
        self.flags.addFlags(eleFile)
        # Modification timestamp for dependency tracking.
        self.timestamp = str(os.path.getmtime(self.path))
        # Success.
        self.initialized = True

###############################################################
# Find the proper <configuration> element, <toolchain> element.
# Mark as 'culled' any that don't match.
# Return with <configuration> element, <toolchain> element,
# and 'ccprefix'
#
def GetConfigAndToolchain(eleRoot:'etree.Element', config:str):
    # Get all the available configurations.
    eleList = eleRoot.findall('configuration')
    # Run through them and check for 'if' conditions.
    # If False, the element will have been marked as culled.
    for eleCfg in eleList:
        result = checkIfElement(eleCfg, True)
        if result is False:
            continue
        if result is None:
            eleString = eleToString(eleCfg)
            print(f'ERROR: Unknown key in <dict>: {eleString}')
            return None, None
    # Rescan to get the list after culling.
    eleList = eleRoot.findall('configuration')
    # Assume we don't find the correct one.
    result = None
    for eleCfg in eleList:
        cfgName = eleCfg.get('name')
        if cfgName == config:
            result = eleCfg
        else:
            eleCfg.tag = 'configuration-culled'
    # Check result.
    if result is None:
        print(f'ERROR:Project configuration {config} not found')
        return None, None
    # Got the configuration.
    eleCfg = result
    # Get the toolchain name.
    eleToolchain = eleCfg.find('toolchain')
    if eleToolchain is None:
        print(f'ERROR:Project configuration {config} has no toolchain specified')
        return None, None
    toolChainName = eleToolchain.text
    # Get all the available toolchains.
    eleList = eleRoot.findall('toolchain')
    # Assume we don't find it.
    result = None
    for eleToolchain in eleList:
        name = eleToolchain.get('name')
        if name == toolChainName:
            result = eleToolchain
        else:
            eleToolchain.tag = 'toolchain-culled'
    # Check result.
    if result is None:
        print(f'ERROR:Project configuration {config} toolchain {toolChainName} not found')
        return None, None
    # Got the toolchain.
    eleToolchain = result
    # Found both; return.
    return eleCfg, eleToolchain

###############################################################
# Configuration class.
# Has all the data needed for compile/link.
#
class Config:
    def __init__(self, build:'Build', eleRoot:'etree.Element', eleCfg, eleToolchain):

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
        # Set default configuration data.
        #######################################################

        # Default configuration data.
        self.toolChainName = 'native'
        self.compilerPath = None
        self.compilerPrefix = None
        self.ccPrefix = ''
        self.optimization = '-O0'
        self.debugging = '-g3'
        # Flags.
        self.flags = Flags()
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
        # Get project specific compiler flags.
        #######################################################

        self.flags.addFlags(eleRoot)

        #######################################################
        # Get configuration specific compiler flags.
        #######################################################

        self.flags.addFlags(eleCfg)

        #######################################################
        # Get toolchain specific compiler flags.
        #######################################################

        self.flags.addFlags(eleToolchain)

        #######################################################
        # Toolchain name; data is assigned later.
        #######################################################
        
        # Get toolchain name.
        self.toolChainName = eleToolchain.get('name')
        result = addToolChain(self, eleToolchain)
        if not result:
            return

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
                if ele.text is None:
                    continue
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
#
def doVarsub(ele:'etree.Element', required:bool=True)->bool:
    attrList = ele.attrib
    for attr in attrList:
        value = varSub(ele.attrib[attr], required)
        if value is None:
            return False
        ele.attrib[attr] = value
    if ele.text is not None:
        value = varSub(ele.text, required)
        if value is None:
            return False
        ele.text = value
    return True

# Recursively replace the keys in the element, and all
# it's children.
# Raises an exception if varSub() fails: key not found.
#
def replaceKeys(ele:'etree.Element', required:bool=True)->bool:
    if not 'Comment' in str(ele.tag):
        if not 'culled' in ele.tag:
            if not doVarsub(ele, required):
                raise ValueError(gError)
    eleList = ele.getchildren()
    for child in eleList:
        # Ignore comments.
        if 'Comment' in str(child.tag):
            continue
        if 'culled' in child.tag or 'added' in child.tag:
            continue
        if not doVarsub(child, required):
            raise ValueError(gError)
        replaceKeys(child, required)
    return True

# Here to check if an 'if' attribute value is true or false.
# The 'if' attribute can be:
#   if="value"       True if value != 0 else False
#   if="key==value"  True if key == value else False
#   if="key!=value"  True if key != value else False
#
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

# Courtesy of ChatGPT: an expression evaluator that allows
# grouping of logical expressions with '()'.
# 
def complexIfCheck(expression)->bool:
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

# Checks an element for if="condition".
# 'condition' can be:
#   {key}
#   {key}==0/1 or {key}!=0/1
#   {key}=={key} or {key}!={key}
#   {key};and;{key} or {key};or;{key}
#   Logical groupings with () of the above
# Returns:
#   True    If not conditional
#   True    If condition is True
#   False   If condition is False
#   False   If not required and a {key} is undefined.
#   None    If required and a {key} is undefined.
#
def checkIfElement(ele:etree.Element, required:bool=False)->bool:
    # Return True if the element is not conditional.
    if 'if' not in ele.attrib:
        return True
    # See if the condition can be evaluated.
    condition = ele.get('if')
    condition = varSub(condition, required)
    # If there is an undefined {key}
    if condition is None:
        # Return either None or False
        return None if required else False
    # Replace the key.
    ele.attrib['if'] = condition
    # Mark and return if evaluated as False.
    if not checkIfTag(condition):
        ele.tag = f'{ele.tag}-culled'
        return False
    # Has 'if' evaluated as True
    return True

# Recursively rename tags that have an 'if' attribute
# that evaluates to False.
# Renaming these tags effectively removes them
# from the XML configuration file.
#
def processIfAttributes(element:'etree.Element'):
    # Ignore comments.
    if 'Comment' not in str(element.tag):
        # If the element has not already been culled or added.
        if 'culled' not in element.tag and 'added' not in element.tag:
            # Check for an 'if' attribute; will cull if false.
            checkIfElement(element)
    # Recursively traverse the child elements.
    for child in element:
        processIfAttributes(child)

# The Build object holds all the information from
# the command line and the XML configuration file.
# It's properties define everything needed for a build.
# It's methods perform the steps needed for a build.
#
class Build:
    def __init__(self,
                 cfgfile:str,
                 config:str,
                 clean:bool=False,
                 prebuilds:bool=False,
                 subs:'list[str]' = [],
                 incs:'list[str]' = [],
                 subDict:dict = None,
                 singleFile:str = None) -> None:

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
        
        # Save xml tree.
        self.root = root

        # Assign values from command line.
        self.configFile = cfgfile
        self.configuration = config
        self.makeClean = clean
        self.prebuilds = prebuilds
        self.subs = subs
        if singleFile is not None:
            if not singleFile.endswith('.s') and not singleFile.endswith('.c') and not singleFile.endswith('.cpp'):
                print(f'Unable to compile {singleFile}: wrong file type, need .s,.c,.cpp')
                return
            # Set clean to force compile.
            self.makeClean = True
        self.singleFile = singleFile

        #######################################################
        # Add <dict> entries that will be defined based on
        # the configuration.
        #######################################################
        varSubDict['config'] = self.configuration
        # Will be defined after the toolchain is added.
        varSubDict['ccprefix'] = '_undefined_'

        # Add command line key:value pairs to variable substitution.
        for kvp in subs:
            parts = kvp.split(':')
            if len(parts) != 2:
                print(f'ERROR: Invalid key:value pair {kvp}')
                return
            varSubDict[parts[0]] = parts[1]

        # Add key:value dictionary if supplied.
        if subDict is not None:
            for sub in subDict:
                varSubDict[sub] = subDict[sub]

        # Add <dict> elements if XML files specified by '-i' parameter.
        # Root element of this file must have tag name 'dicts'.
        for inc in incs:
            # Sanity check.
            inc = inc.strip()
            if not os.path.exists(inc):
                print(f'ERROR: XML include file not found: {inc}')
                return
            # Parse it.
            incTree, incRoot = parseFile(inc)
            if incRoot is None:
                print(f'ERROR: Unable to parse XML include file: {inc}')
                return
            # Check root tag name.
            if incRoot.tag != 'dicts':
                print(f'ERROR: Root of include file {inc} does not have "dicts" tag')
                return
            # Add dictionay entries; no {keys} allowed.
            print(f'Adding dictionary file {inc}')
            addDicts(varSubDict, incRoot, True)

        # Apply any operations to be done before we proceed.
        # <pre_op> elements must have defined {keys}.
        opList = root.findall('pre_op')
        for op in opList:
            # Check element for conditional; MUST be evaluated if present.
            result = checkIfElement(op, True)
            # NONE: Conditional present with undefined {key}.
            if result is None:
                eleString = eleToString(op)
                print(f'ERROR: Unknown key in <pre_op>: {eleString}')
                return
            # FALSE: Conditional present, and evaluates to False; has been be marked 'culled'.
            if result is False:
                continue
            # TRUE: Conditional not present or IS present and evaluates to True
            cmd = op.text
            cmd = varSub(cmd, True)
            if cmd is None:
                print(f'ERROR: Unknown key in <pre_op>: {op.text}')
                return
            op.text = cmd
            failed = False
            result = os.system(cmd)
            flag = op.get('result')
            if flag is not None:
                flag = int(flag)
                failed = flag != result
            if not failed:
                print(f'<pre_op> command : {cmd} : returned {result}')
            else:
                print(f'ERROR: <pre_op> command : {cmd} : returned {result}')
                return
            op.tag = f'{op.tag}-added'

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
            # Check for conditional; complete evaluation is required.
            result = checkIfElement(inc, True)
            # NONE: Conditional present with undefined {key}.
            if result is None:
                print(gError)
                return
            # FALSE: Conditional present, and evaluates to False; has been be marked 'culled'.
            if result is False:
                continue
            # TRUE: Conditional not present or IS present and evaluates to True
            pathText = inc.text.strip()
            incPath = varSub(pathText, True)
            if incPath is None:
                print(gError)
                return
            if not os.path.exists(incPath):
                print(f'Include file {pathText} not found')
                return
            incTree,incRoot = parseFile(incPath)
            if incTree == None:
                print(f'Error parsing include file: {cfgfile}')
                return
            # If only <dict> elements, add them directly to the dictionary.
            if incRoot.tag == 'dicts':
                print(f'Adding <dict> elements from {incPath}')
                addDicts(varSubDict, incRoot)
            # Else include all as part of configuraion.
            else:
                print(f'Adding include file {incPath}')
                # Append include file data.
                for child in incRoot:
                    root.append(deepcopy(child))
            # Mark as added.
            inc.tag = f'{inc.tag}-added'
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)
        
        # Get the <configuration> and <toolchain> elements
        # for this project. Non-matching elements will be
        # marked 'culled'.
        self.eleCfg, self.eleToolchain = GetConfigAndToolchain(root, config)
        if self.eleCfg is None:
            return
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)
        
        #######################################################
        # At this point, the XML files have all been processed
        # and unused <configuration> and <toolchain> elements
        # have been marked 'culled'.
        # We can now run through the entire file and add all
        # the <dict> elements.
        #######################################################
        
        # Recursively get all <dict> elements added to varSubDict.
        # These may be used directly below by <include> elements.
        # {keys} can be undefined.
        try:
            addDicts(varSubDict, root)
        except ValueError as err:
            print(err)
            return

        # We are now finished adding raw <dict> entries.
        # <dict> values may themselves have {key} entries.
        # Example:
        #   <dict key="tool">myTool</dict>
        #   <dict key="fooTool">foo/{tool}</dict>
        # Verify that there are no undefined variable substitutions.
        # This will ERROR if any undefined keys (except if undefined).
        for key in varSubDict:
            value = varSub(varSubDict[key], False)
            if value is None:
                print(gError)
                return
            varSubDict[key] = value
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)

        # We have all the <dict> elments resolved.
        # Now we need to recursively traverse the XML file
        # and replace all the {key} values.
        # It is possible for an undefined {key} to be present
        # in a tag; in this case, an exception will be raised,
        # and we bail.
        try:
            replaceKeys(root, True)
        except ValueError as err:
            print(err)
            return
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)

        # Recursively process 'if' attribute logic for all elements.
        # We rename all tags as <culled> that have an 'if'
        # attribute that evaluates to False.
        try:
            processIfAttributes(root)
        except ValueError as err:
            print(err)
            return
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)

        # At this point, the XML file is complete with all
        # included files and <dict> values evaluated.
        # We now proceed to process the file.

        #######################################################
        # Process configuration data.
        # Collects flags, includes, objects, sources, etc.
        #######################################################
        self.cfg = Config(self, root, self.eleCfg, self.eleToolchain)
        if not self.cfg.initialized:
            return

        # If compiling single file; error if it's in the source list.
        if self.singleFile is not None:
            found = False
            src:SourceFile
            for src in self.cfg.sources:
                if src.filename == self.singleFile:
                    found = True
                    break
            if not found:
                print(f'Single file {self.singleFile} not in source file list')
                return
        
        #######################################################
        # Assign any <dict> values that are 'undefined'
        #######################################################
        varSubDict['ccprefix'] = self.cfg.ccPrefix

        # Do final substitution all must be defined.
        try:
            replaceKeys(root, True)
        except ValueError as err:
            print(err)
            return
        
        # Show the work.
        if printIntermediateXml:
            tree.write('eraseme.xml' , pretty_print=True)

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
                            [],         # Inc files
                            varSubDict,
                            None)       # Single file
            # Return.
            os.chdir(cur_dir)
            if result != 0:
                print(f'Unable to pre-build project in {proj.path}')
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
            if self.singleFile is None:
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
                for define in self.cfg.flags.a:
                    ccmd += f' {define}'
                for define in srcFile.flags.a:
                    ccmd += f' {define}'
            # Else C/C++.
            else:
                # Add common C/C++ flags.
                for define in self.cfg.flags.cc:
                    ccmd += f' {define}'
                for define in srcFile.flags.cc:
                    ccmd += f' {define}'
                # Add C flags.
                if srcFile.type == FileType.CFILE:
                    for define in self.cfg.flags.c:
                        ccmd += f' {define}'
                    for define in srcFile.flags.c:
                        ccmd += f' {define}'
                # Add C++ flags.
                else:
                    for define in self.cfg.flags.cpp:
                        ccmd += f' {define}'
                    for define in srcFile.flags.cpp:
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
            # We're adding an output prefix as a niche feature (libmicrohttpd).
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
                for flag in self.cfg.flags.l:
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
            for ldefine in self.cfg.flags.l:
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
        opList = self.root.findall('post_op')
        for op in opList:
            cmd = op.text
            failed = False
            result = os.system(cmd)
            flag = op.get('result')
            if flag is not None:
                flag = int(flag)
                failed = flag != result
            if not failed:
                print(f'<post_op> command : {cmd} : returned {result}')
            else:
                print(f'ERROR: <post_op> command : {cmd} : returned {result}')
                return 1
        return 0

###############################################################
# Entry point for pyMake.
# Creates a PyMakeBuild object and builds the project(s)
#
def pyMake(cfgfile:str,
           config:str,
           clean:bool,
           prebuilds:bool,
           subs:'list[str]' = [],
           incs:'list[str]' = [],
           subDict:dict = None,
           singleFile:str = None)->int:

    # Where are we?
    print(f'\npyMake executing in {os.getcwd()}')

    # Create the build object.
    try:
        build = Build(cfgfile, 
                      config, 
                      clean, 
                      prebuilds, 
                      subs, 
                      incs, 
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
    if (build.cfg.library and not needLink) or (singleFile is not None):
        if singleFile is None:
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
    parser.add_argument('-s', '--sub',      help='Semicolon delimited variable substitution key:value pair',action='append',default=[])
    parser.add_argument('-i', '--inc',      help='Include XML <dicts> file:                 ',action='append',default=[])
    parser.add_argument('-x', '--xml',      help='Print intermediate pyMake.xml iterations: default=False',action='store_true')
                    
    try:
        args = parser.parse_args()

        args.file = args.file.strip()
        args.cfg = args.cfg.strip()
        for i in range(len(args.sub)):
            args.sub[i] = args.sub[i].lstrip()
        for i in range(len(args.inc)):
            args.inc[i] = args.inc[i].lstrip()
        args.one = args.one.strip()

        # Return here if just version.
        if args.version:
            print(f'pyMake.py version {REVISION}')
            sys.exit(0)

        # Save for return.
        cwd_main = os.getcwd()

        # Change if not compiling a single file.
        if args.one == 'None':
            args.one = None
        # Whether compiling a single file or a project, we must
        # be able to locate the pyMake XML file. We start at the
        # current location and move up the path until we find
        # the correct file.
        xmlFound = False
        while True:
            # print(os.getcwd())
            if os.path.exists(args.file):
                xmlFound = True
                break
            cur_dir = os.getcwd()
            # Break if we can't get any higher.
            if cur_dir == '/':
                break
            os.chdir('../')
        if not xmlFound:
            print(f'ERROR: Cannot find XML configuration file {args.file}')
            sys.exit(1)

        # If we get here, the pyMake XML file is in the current folder.

        # Pring intermediate xml files?
        printIntermediateXml = args.xml

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
        print(f'    xml:            {args.xml}')

        # Execute and back.
        retval_main = pyMake(args.file, 
                             args.cfg, 
                             args.clean, 
                             args.prebuild, 
                             args.sub,      # Variable subs or emtpy array 
                             args.inc,      # File names or empty array
                             None,          # No dictionary from command line
                             args.one)      # File name or None
        os.chdir(cwd_main)
        print(f'\npyMake exiting with code {retval_main}')
        sys.exit(retval_main)

    except Exception as e:
        print(f'ERROR: pyMake: Exception encountered: {e}')
        os.chdir(cwd_main)
        sys.exit(1)
