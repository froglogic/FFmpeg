import conans
import filecmp
import io
import os
import re
import shutil

class FfmpegConan(conans.ConanFile):
    name = 'ffmpeg'
    version = '4.4'
    settings = 'os', 'arch', 'build_type'
    requires = 'openh264/2.1.1@froglogic/dist'
    generators = 'pkg_config'
    no_copy_source = True
    win_bash = True
    revision_mode = 'scm'
    python_requires = 'dockerRecipe/1.0.0@froglogic/util'
    python_requires_extend = 'dockerRecipe.DockerRecipe'

    options = {
        'buildSuffix': 'ANY',
    }
    default_options = {
        'buildSuffix': None,
    }
    scm = {
        'type': 'git',
        'url': 'auto',
        'revision': 'auto',
    }
    docker = {
        'Linux': {
            'image': '/squishbuild/centos6:6.8.0',
        },
    }

    def __libLinkExt(self):
        if self.settings.os == 'Windows':
            return 'lib'
        elif self.settings.os == 'Linux':
            return 'so'
        elif self.settings.os == 'Macos':
            return 'dylib'
        else:
            return ''

    def configure(self):
        if self.settings.os != 'Windows':
            self.build_requires = [ 'nasm/2.10.09@froglogic/util' ]
        if self.settings.os == 'Macos':
            del self.settings.arch

    def cmdPrefix(self):
        if self.settings.os == 'Linux':
            paths = [*self.deps_cpp_info['nasm'].bin_paths, '$PATH']
            return 'export PATH=' + ':'.join(paths) + '; '
        elif self.settings.os == 'Windows':
            return 'export PKG_CONFIG_PATH=' + conans.tools.unix_path(self.build_folder, path_flavor='msys2') + ' && '
        else:
            return ''

    def buildFor(self, arch, prefix):
        configure = os.path.join(self.source_folder, 'configure')
        configure = conans.tools.unix_path(configure, path_flavor='msys2')
        prefix = conans.tools.unix_path(prefix, path_flavor='msys2')
        cmd = self.cmdPrefix()
        cmd += configure
        cmd += ' --prefix=%s' % prefix
        cmd += ' --disable-static'
        cmd += ' --enable-shared'
        cmd += ' --disable-all'
        cmd += ' --disable-autodetect'
        cmd += ' --enable-ffmpeg'
        cmd += ' --disable-doc'
        cmd += ' --enable-avcodec'
        cmd += ' --enable-avformat'
        cmd += ' --enable-swscale'
        cmd += ' --disable-everything'
        cmd += ' --enable-libopenh264'
        cmd += ' --enable-encoder=libopenh264'
        cmd += ' --enable-protocol=file'
        cmd += ' --enable-muxer=mp4'
        cmd += ' --enable-debug'
        cmd += ' --disable-rpath'
        cmd += ' --disable-stripping'
        cmd += ' --install-name-dir=@rpath'

        if self.options.buildSuffix:
            cmd += ' --build-suffix=%s' % self.options.buildSuffix

        if self.settings.os == 'Windows':
            cmd += ' --toolchain=msvc'

        archMap = {
            'x86': 'i686',
            'x86_64': 'x86_64',
            'armv8': 'arm64',
        }

        cmd += ' --arch=%s' % archMap[str(arch)]

        if self.settings.os == 'Linux' and arch == 'x86':
            cmd += ' --extra-cflags=-m32'
            cmd += ' --extra-cxxflags=-m32'
            cmd += ' --extra-ldflags=-m32'

        if self.settings.os == 'Macos':
            if arch == 'x86_64':
                compatFlags = '--target=x86_64-apple-darwin17.7.0 -mmacosx-version-min=10.13'
            elif arch == 'armv8':
                compatFlags = '--target=arm64-apple-darwin20.5.0 -mmacosx-version-min=11.0'
            else:
                raise Exception('Unsupported architecture: %s' % arch)

            cmd += ' "--extra-cflags=%s"' % compatFlags
            cmd += ' "--extra-cxxflags=%s"' % compatFlags
            cmd += ' "--extra-ldflags=%s"' % compatFlags

        self.run(cmd)

        cmd = self.cmdPrefix()
        cmd += 'make -j%d' % conans.tools.cpu_count()
        self.run(cmd)

    def buildWindows(self):
        msvcEnv = conans.client.tools.vcvars_dict(self, compiler_version='14')
        for name, value in msvcEnv.items():
            if isinstance(value, list):
                value = os.pathsep.join(value)
            os.environ[name] = value
        self.buildFor(self.settings.arch, self.package_folder)

    def buildLinux(self):
        os.environ['PKG_CONFIG_PATH'] = self.build_folder
        self.buildFor(self.settings.arch, self.package_folder)

    def buildMacos(self):
        os.environ['PKG_CONFIG_PATH'] = self.build_folder
        bld = os.path.join(self.build_folder, 'x86_64')
        os.mkdir(bld)
        os.chdir(bld)
        self.buildFor('x86_64', self.package_folder)

        bld = os.path.join(self.build_folder, 'armv8')
        os.mkdir(bld)
        os.chdir(bld)
        self.buildFor('armv8', bld + '-install')
        cmd = self.cmdPrefix()
        cmd += 'make install'
        self.run(cmd)

    def build(self):
        getattr(self, 'build%s' % self.settings.os)()

    def compareDirs(self, dir1, dir2):
        for entry in os.scandir(dir1):
            entry2 = os.path.join(dir2, entry.name)
            if entry.is_dir():
                if os.path.islink(entry2) or not os.path.isdir(entry2):
                    raise Exception("Difference: %s is not a directory" % entry2)
                self.compareDirs(entry.path, entry2)

            elif entry.is_file() and not entry.is_symlink():
                if os.path.islink(entry2) or not os.path.isfile(entry2):
                    raise Exception("Difference: %s is not a file" % entry2)
                if not filecmp.cmp(entry.path, entry2, shallow=False):
                    raise Exception("Difference: %s and %s are different" % (entry.path, entry2))

    def query(self, *args, **kwargs):
        outBuf = io.StringIO()
        kwargs['output'] = outBuf
        self.run(*args, **kwargs)
        return outBuf.getvalue().strip()

    def package(self):
        if self.settings.os == 'Macos':
            bld = os.path.join(self.build_folder, 'x86_64')
            os.chdir(bld)
        
        cmd = self.cmdPrefix()
        cmd += 'make install'
        self.run(cmd)

        suffix = self.options.buildSuffix if self.options.buildSuffix else ''
        libPrefix = '' if self.settings.os == 'Windows' else 'lib'
        linkPattern = re.compile('^(%s[^.]+)%s(\\.%s)$' % (libPrefix, suffix, self.__libLinkExt()))
        libraryPattern = re.compile('^%s.+%s\\..+\\.%s$' % (libPrefix, suffix, self.__libLinkExt()))

        libdir = 'bin' if self.settings.os == 'Windows' else 'lib'
        libdir = os.path.join(self.package_folder, libdir)
        if self.options.buildSuffix:
            for entry in os.scandir(libdir):
                match = linkPattern.match(entry.name)
                if match:
                    linkPath = os.path.join(libdir, match.group(1) + match.group(2))
                    shutil.copy(entry.path, linkPath, follow_symlinks=False)

        if self.settings.os == 'Macos':
            armLibdir = os.path.join(self.build_folder, 'armv8-install', 'lib')
            for entry in os.scandir(libdir):
                if entry.is_file() and not entry.is_symlink():
                    if libraryPattern.match(entry.name):
                        tmpName = entry.path + '-tmp'
                        armName = os.path.join(armLibdir, entry.name)
                        os.rename(entry.path, tmpName)
                        self.run('lipo %s %s -create -output %s' % (tmpName, armName, entry.path))
                        os.unlink(tmpName)

                        self.run('install_name_tool -add_rpath "@loader_path" "%s"' % entry.path)


            incdir = os.path.join(self.package_folder, 'include')
            armIncdir = os.path.join(self.build_folder, 'armv8-install', 'include')
            self.compareDirs(incdir, armIncdir)
            self.compareDirs(armIncdir, incdir)
