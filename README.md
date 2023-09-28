# pyMake

pyMake.py is an application that will compile and link a C/C++ project defined by a single XML project file.

No make files are involved.

pyMake reads the configuration XML file (default=pyMake.xml) and then executes the compile and link  
commands that are needed to build the project.

The main features of pyMake are:

- All data needed to build a project can be contained in one XML file

- The \<include> tag allows files with additional XML configuration to be added

- Variable substitution allows text to be replaced in any element

- An 'if' attribute can be added to any element to include/exclude it from the compilation process.

- Supports multiple toolchains

- Supports multiple configurations

- Will invoke pyMake on other projects needed for linking

- Generates and uses dependency data when compiling.

## Why you might want to use pyMake and Visual Studio Code

- IDE's hide configurations in complex XML or binary files  
- IDE's change, and can break projects  
- pyMake defines all project data in readable XML format  
- vscode is agnostic about a build system  
- vscode does a great job of analyzing code  
- vscode has features to build and debug both projects and pyMake itself  
- You can modify and debug pyMake.py if you need to add/delete features

## Installation

You should copy pyMake.py to a folder in your $PATH environment variable and  
make it executable; /usr/local/bin is a good choice.

To see the command line options availalbe to pyMake, execute:
```
>pyMake.py --help
usage: pyMake.py [-h] [-v] [-c] [-p] [-f FILE] [-g CFG] [-o ONE] [-s SUB] [-i INC] [-x]

Compiles an application as specified in the configuration XML file

options:
  -h, --help            show this help message and exit
  -v, --version         Show version number: default=False
  -c, --clean           Clean before building: default=False
  -p, --prebuild        Execute recursive pyMake on prebuilds: default=False
  -f FILE, --file FILE  XML Configuration file to use: default=pyMake.xml
  -g CFG, --cfg CFG     Build configuration used in XML file: default=Release
  -o ONE, --one ONE     Compile just the specified file: default=None
  -s SUB, --sub SUB     Append semicolon delimited key/value pair to variable substitution dictionary
  -i INC, --inc INC     Include XML <dict> file(s): default=None
  -x, --xml             Print intermediate pyMake.xml iterations (for debugging pyMake): default=False

Example: pyMake.py -c -p -g Debug -s target:x86 -o main.c
```
It is recommended to use Visual Studio Code for working with pyMake.  
The 'workspace/.vscode' folder provides support for compiling a single file  
or a complete project, and for debugging pyMake.py itself.

The recommended vscode workspace structure is:

```
/workspace
    .clang-format                   <--For formatting C/C++ source code in vscode
    /.vscode                        <--VSCode folder with tasks, launch, and c_cpp_preferences
    /project
        /src                        <--Headers & source files
            header.h
            source1.c
            source2.cpp
        /Release                    <--Output folder: Release configuration
            executable or library
            /src
                intermediate files
        pyMake.xml                  <--Project description XML file
    More projects
```
## Compiling a project

In the example "HelloWorldSimplest", the pyMake.xml file is:
```
<!-- 
    Example of hello world that will be compiled 
    to a native x86 application.
    It uses an <include> element to bring in the x86
    native toolchain and configurations.
-->
<project artifact="hello" type="executable">
    <include>../pyIncX86LinuxNative.xml</include>
    <sources>
        <file path="src/hello.cpp"/>
    </sources>
</project>
```
On the command line in the HelloWorldSimplest folder, execute:
```
pyMake.py
```
Compilation will create a /Release folder in HelloWorldSimplest, and the executable named
'hello' will be placed in that folder.

To clean the build before compilation, execute:
```
pyMake.py -c
```
Compilation will remove and re-create the Release folder and build the executable.  
Note that if the source file has been changed, executing just 'pyMake.py' will  
recompile and link the application.

The best way to try pyMake is by executing the supplied examples.

## Variable substitution

Variable substitution is done with key:value pairs either specified on the command line  
with '-s' parameters, an XML file with \<dict> elements specified with the command line 
'-i' parameter, in an \<include>'ed XML with \<dict> elements, or with \<dict> elements  
in the pyMake.xml file itself.

NOTE: The configuration, typically Release or Debug, is made available for variable  
substitution as the {config} key.

Examples:
```
    Command line:   -s keyText:keyValue -and/or- -i MyDicts.xml 
    XML tag:        <dict key="keyText">keyValue</dict>
```
In all cases "keyValue" will be substituted for "{keyText}" anywhere in the XML  
configuration file when pyMake is executed.
If an XML file is specified with the '-i' command line parameter, the XML file  
must have a root tag of \<dicts>, and only contain other \<dict> elements.

Example \<dict> XML file:
```
    <dicts>
        <dict key="foo">abc</dict>
        <dict key="bar">def</dict>
    </dict>
```
## Conditional compilation with the 'if' attribute

An XML element can be enabled or disabled using the 'if' attribute. The value of the  
'if' attribute is evaluated during the build as either true or false. If false, the  
element will not be used.

Examples:
```
    <dict key="boolKeyT>1</dict>
    <dict key="boolKeyF>0</dict>
    <dict key="textKey1">param1</dict>
    <dict key="textKey2">param2</dict>

    <ccflag if="{boolKeyT}">-D_RELEASE</ccflag>           // Enabled
    <ccflag if="{boolKeyF}">-D_DEBUG</ccflag>             // Disabled
    <ccflag if="{textKey1}==param2">-D_RELEASE</ccflag>   // Disabled
    <ccflag if="{textKey2}!=qwerty">-D_FLAGX</ccflag>     // Enabled

    <ccflag if="{boolKeyF}!=0">-D_TEST</ccflag>           // Disabled
    <ccflag if="{textKey1}==param1">-D_TESTING</ccgflag>  // Enabled
```
The 'if' attribute can contain logical expressions using ";and'", ";or;" and "()":
```
    <include if="({key1};or;{key2}==value2);and;{key3}>somefile.xml</include>
```
## pyMake.xml Configuration file structure

The structure of pyMake.xml was inspired by watching the output of a compilicated
make file build for an AT91SAM7X ARM cross compiled project. Cross compiled embedded
systems are notorious for needing obscure compiler flags as well as other
flags that are specific to the processor or system.

```
<!-- 
    Primary <dict> entries are taken from '-s' command line parameters,
    or from an XML file included with the '-i' command line parameter.
    These <dict> entries cannot contain variable substitutions ({key}).
    Examples:
        -s tools:armTools
        -s cfgs:armCfgs
        -s dicts:1

        -i myDicts.xml  (Root element mus be <dicts>)

    NOTE: The {config} key is built in and is set to the configuration
          used for the build; typically 'Debug' or 'Release'  
          The {ccprefix} key is built in and is the "toolchain-prefix"  
          for the selected toolchain. It is the complete path to the  
          executables. Use it in <post_op> elements to invoke a specific
          executable: {ccprefix}objcopy for instance.
-->
<!-- 
    The project artifact can include an extension; someFcn.bin, someFcn.exe, etc.
    Project type can be 'executable' or 'library'.
    If the name has no extension, and the type is 'library', we assume
    the user wants a static library with prefix 'lib', and extension '.a'.
-->
<project artifact="someFcn" type="executable">
    <!-- 
        Top level <dict> entries.
    -->
    <dict key="boolKey">0</dict>
    <dict key="textKey">XYZ</dict>
    <!--
        Top level compiler flags.
    -->
    <ccflag>-D_SOMETHING_</ccflag>
    <!--
        Command line (Python os.system()) operations to be done before
        and after compile and link.
        <pre_op> commands are executed after processing any keys supplied on the
        command line and before any other keys are processed.
        Both <pre_op> and <post_op> command can use variable substitution.
    -->
    <pre_op>some-prebuild-operation</pre_op>
    <post_op>{ccprefix}ranlib lib.a</post_op>
    <!-- 
        The artifact extension can be explicitly or conditionally set:
            bin, hex, exe, a, so, dll
    -->
    <extension if="{target}==win32">exe</extension>
    <!-- 
        XML files with configuration data added to this file.
        Here, the keys 'dicts','tools', and 'cfgs' would need to be
        supplied on the command line as shown above.
    -->
    <include if="{dicts}">../pyIncSomeDicts.xml</include>            // Enabled
    <include if="{tools}==armTools">../pyIncArmTools.xml</include>   // Enabled
    <include if="{tools}==x86Tools">../pyIncX86Tools.xml</include>   // Disabled
    <include if="{cfgs}==armCfgs">../pyIncArmConfigs.xml</include>   // Enabled
    <include if="{cfgs}==x86Cfgs">../pyIncX86Configs.xml</include>   // Disabled
    <!-- 
        Toolchain definition; there can be multiple toolchains.
    -->
    <toolchain name="arm-linux-gnueabihf">
        <compilerPath>/usr/bin</compilerPath>
        <compilerPrefix>arm-linux-gnueabihf-</compilerPrefix>

        <ccflag>-fpack-struct</ccflag>      <!-- Common C/C++ compiler flags -->
        <aflag>-gdwarf-2</aflag>            <!-- Assembly flags -->
        <cflag>-D_POSIX_SOURCE</cflag>      <!-- C specific flags -->
        <cppflag>-fno-exceptions</cppflag>  <!-- C++ specific flags -->
        <lflag>-static</lflag>              <!-- Linker flags -->
    </toolchain>
    <!-- 
        Configuration definition; there can be multiple configurations.  
        Typically, the configurations will be 'Debug' or 'Release', but  
        can be any name.
    -->
    <configuration name="Release">
        <toolchain>arm-linux-gnueabihf</toolchain>
        <optimization>-O1</optimization>
        <debugging>-g3</debugging>
        <!-- Configuration specific compiler/linker flags -->
    </configuration>
    <!-- 
        Objects to be linked along with source files.
    -->
    <objects>
        <obj>../Lib1/{config}/libFcn1.a</obj>
        <obj>../Lib2/Release-test/libFcn2.a</obj>   <!-- See prebuild for Lib2 below -->
        <obj>-lpthread</obj>
    </objects>
    <!-- 
        Projects to be built before compilation.
        These would typically provide libraries.
        Prebuild projects receive the command line arguments provided to
        the top level build. You can override these arguments as shown
        below for the project 'Lib2'.
    -->
    <prebuilds>
        <project path="../Lib1"/>
        <project path="../Lib2">
            <!-- Build parameters that differ from top level -->
            <configfile>pyMake-Test.xml</configfile>
            <configuration>Release-test</configuration>
            <clean>0/1</clean>
            <prebuilds>0/1</prebuilds>
            <sub>key1:value1</sub>
            <sub>key2:value2</sub>
        </project>
    </prebuilds>
    <!-- 
        Where to look for header files.
    -->
    <includes>
        <path>../Lib1/src</path>
        <path>../Lib2/src</path>
    </includes>
    <!-- 
        Source files to be compiled.
        Wildcard '*' will include all source files in the folder.
        Files can be excluded from the wildcard with the <exclude> element.
        Individual optimization and debugging can be specified for any file.
    -->
    <sources>
        <file path="../common/src/fcn1.c"/>
        <file path="src/*"/>
        <file path="lcl/*">
            <exclude>foo.c</exclude>
            <exclude>bar.cpp</exclude>
            <ccflag>-D_SOMETHING_ELSE_</ccflag>  <!-- Source file specific compiler flags -->
        </file>
        <file path="somepath">
            <optimization>-O2</optimization>
            <debugging>-g2</debugging>
        </file>
    </sources>
</project>
```
## Working with Visual Studio Code

#### The vscode extensions used for these examples are:
```
    Auto Rename Tag
    autopep8
    C/C++
    C/C++ Extension Pack
    C/C++ Themes
    JavaScript and TypeScript
    Jupyter
    Jupyter Cell Tags
    Jupyter Keymap
    Pylance
    Python
    XML
    XML Tools
```

### Contents of .vscode folder

#### tasks.json (building)

This tasks in this file are for building an individual file, or for compiling  
and building a complete project. Refer to the comments for each task.

#### launch.json (debugging)

This launch configurations in this file allow you to debug an application, or  
to debug pyMake.py itself. Refer to the comments for each configuration.

#### c_cpp_properties (editing)

The configurations in this file allow you to specify the environment for working  
with various compilers. You can specify #define values, include paths, and other  
data specific to a toolchain.  
In the supplied c_cpp_properties file, you can see how five different toolchains  
are supported: Linux x86 native, Linux on the Raspberry Pi, WIN32 using mingw,  
ARMv7 Bare Metal, and Atmel (now Microchip) AVR.
