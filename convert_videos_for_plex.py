import math
import os
import shutil
import subprocess
import sys
import timeit
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from enum import Enum
from io import BytesIO
from pathlib import Path
import textwrap
from statistics import mean
from zipfile import ZipFile

import requests as requests
from pymediainfo import MediaInfo

MILLISEC_TO_MIN = 60000
FATAL_ERROR = 127


class COLOR(str, Enum):
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

    def write(self, string: str) -> str:
        return f'{self.value}{string}{self.NC}'


class LockFile:
    def __init__(self, file: 'File'):
        self.lock_file = file.source.with_suffix('.lock')
        self._touched = False

    def __enter__(self) -> 'LockFile':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._touched:
            self.lock_file.unlink()

    def exists(self) -> bool:
        return self.lock_file.exists()

    def touch(self):
        self.lock_file.touch()
        self._touched = True


class File:
    def __init__(self, source: Path, converter: 'Converter'):
        self.source = source
        self.dest = source.with_suffix('.mp4')
        self.converter = converter
        if self.converter.output:
            self.dest = Path(self.converter.output, self.dest.name)
        self.skip = ''
        self.ask = False
        self.run = False

    def check_file(self) -> 'File':
        if self.dest.exists():
            if self.converter.force:
                self.skip = (COLOR.RED.write(f"Overwriting: '{self.dest.name}'"))
            else:
                if self.converter.skip:
                    self.skip = (COLOR.RED.write(f"Skipping (already exists): '{self.dest.name}'"))
                else:
                    self.ask = True
        return self

    def check_info(self) -> 'File':
        self.media_info = MediaInfo.parse(self.source)
        if not self.media_info.video_tracks:
            self.skip = COLOR.RED.write(f"Skipping (missing info): '{self.name}'")
            return self
        format = self.media_info.video_tracks[0].format
        profile = self.media_info.video_tracks[0].format_profile
        self.duration = float(self.media_info.video_tracks[0].duration or 0) / MILLISEC_TO_MIN
        self.duration_min = math.ceil(self.duration / 10) * 10
        if self.converter.audio_track or self.converter.subtitle_track or format in ('HEVC', 'xvid', 'MPEG Video') or self.converter.codec in format or (format == 'AVC' and '@L5' in profile):
            self.run = True
        else:
            self.skip = COLOR.RED.write(f'Skipping (video format {format} {profile} will already play in Plex)')
        return self

    def __lt__(self, other) -> bool:
        return self.source.__str__() < other.source.__str__()

    @property
    def name(self) -> str:
        return self.source.name


class Converter:
    def __init__(self, input: str = '.', output: str = None, workspace: str = None, run: bool = False, skip: bool = False, codec: str = 'MPEG-4', delete_original: bool = False, force: bool = False, audio_track: int = 0, subtitle_track: int = 0, preset: str = 'Fast 1080p30'):
        self.input = Path(input).resolve()
        self.output = Path(output).resolve() if output else None
        self.workspace = Path(workspace).resolve() if workspace else None
        self.run = run
        self.skip = skip
        self.codec = codec
        self.delete_original = delete_original
        self.force = force
        self.audio_track = audio_track
        self.subtitle_track = subtitle_track
        self.preset = preset
        print(COLOR.BLUE.write("TRANSCODING" if self.run else "DRY RUN"))

    def get_files(self) -> list[File]:
        files = []
        exts = ('avi', 'mkv', 'iso', 'img', 'mp4', 'm4v', 'ts')
        exts_len = len(exts)
        for i, ext in enumerate(exts):
            print(COLOR.BLUE.write(f'Finding files step {i + 1} of {exts_len}: '), end='', flush=True)
            amount = len(files)
            for source in self.input.glob(f'**/*.{ext}'):
                file = File(source, self).check_file()
                if not self.force and file.skip:
                    continue
                files.append(file)
            print(COLOR.GREEN.write(f'Found {len(files) - amount}'))
        print(COLOR.GREEN.write(f'{len(files)}'))
        return sorted(files)

    @staticmethod
    def get_handbrake_command():
        if hasattr(Converter.get_handbrake_command, 'command'):
            return Converter.get_handbrake_command.command
        command = 'HandBrakeCLI'
        if sys.platform == 'win32':
            command += '.exe'
            for path in [Path('.').resolve()] + os.path.expandvars('$PATH').split(';'):
                if Path(path, command).exists():
                    break
            else:
                print(COLOR.RED.write(f'{command} not found, downloading'))
                version = requests.get('https://github.com/HandBrake/HandBrake/releases/latest').url.split('/')[-1]
                ZipFile(BytesIO(requests.get(f'https://github.com/HandBrake/HandBrake/releases/download/{version}/HandBrakeCLI-{version}-win-x86_64.zip').content)).extract('HandBrakeCLI.exe')
        else:
            for path in os.path.expandvars('$PATH').split(';'):
                if Path(path, command).exists():
                    break
            else:
                try:
                    subprocess.run(['HandBrakeCLI'], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    print(COLOR.RED.write('HandBrakeCLI is not installed, please install it using the instructions in the README.md'))
                    exit(FATAL_ERROR)
        Converter.get_handbrake_command.command = command
        return command

    # @staticmethod
    # def get_mediainfo_command():
    #     if hasattr(Converter.get_mediainfo_command, 'command'):
    #         return Converter.get_mediainfo_command.command
    #     command = 'mediainfo'
    #     if sys.platform == 'win32':
    #         command = 'MediaInfo.exe'
    #         for path in [Path('.').resolve()] + os.path.expandvars('$PATH').split(';'):
    #             if Path(path, command).exists():
    #                 break
    #         else:
    #             print(COLOR.RED.write(f'{command} not found, downloading'))
    #         ZipFile(BytesIO(requests.get('https://mediaarea.net/download/binary/mediainfo/21.09/MediaInfo_CLI_21.09_Windows_x64.zip').content)).extract('MediaInfo.exe')
    #     else:
    #         for path in os.path.expandvars('$PATH').split(';'):
    #             if Path(path, command).exists():
    #                 break
    #         else:
    #             print(COLOR.RED.write('HandBrakeCLI is not installed, please install it using the instructions in the README.md'))
    #             exit(FATAL_ERROR)
    #     Converter.get_mediainfo_command.command = command
    #     return command

    def convert(self):
        audio = ['--audio', self.audio_track] if self.audio_track else ['--audio-lang-list', 'und', '--all-audio']
        subtitle = ['--subtitle', self.subtitle_track, '--subtitle_burned'] if self.subtitle_track else ['-s', 'scan']
        files = self.get_files()
        count = len(files)
        count_len = len(str(count))
        time_avg = {}
        command = self.get_handbrake_command()
        queue_data = {'times': [], 'durations': []}
        for i, file in enumerate(files):
            if file.skip and not self.force:
                print(file.skip)
                continue
            with LockFile(file) as lock:
                if lock.exists():
                    print(COLOR.RED.write(f"Lockfile for '{file.name}' exists, skipping"))
                    continue
                if self.run:
                    lock.touch()
                eta = ''
                if len(queue_data['times']) >= 2:
                    duration = math.ceil(mean(queue_data['durations']) / 10) * 10
                    for dur, avg in time_avg.items():
                        if dur == duration:
                            avg = mean(avg)
                            break
                    else:
                        avg = mean(queue_data['times'])
                    eta = f' [Queue ETA: ~{(avg * (count - i)) / 60:.0f} min]'
                i += 1
                print(COLOR.BLUE.write(f"Checking [{i.__str__().rjust(count_len, '0')}/{count} ({i/count:.0%})]: '{file.name}'{eta}"))
                file.check_file()
                if file.skip and not self.force:
                    print(file.skip)
                    continue
                new_file = file.dest
                if file.ask:
                    while reply := input(f"'{new_file.name}' already exists, do you wish to overwrite it [y|n]? ").lower() in ('y', 'n'):
                        pass
                    if reply == 'y':
                        print(COLOR.RED.write(f"Overwriting: '{new_file.name}'"))
                    elif reply == 'n':
                        print(COLOR.RED.write(f"Skipping (already exists): '{new_file.name}'"))
                        continue
                file.check_info()
                if file.run:
                    eta = ''
                    duration = file.duration_min
                    if len(time_avg.get(duration, [])) >= 2:
                        eta = f' [ETA: ~{mean(time_avg[duration]) / 60:.0f} min]'
                    print(COLOR.BLUE.write(f"Transcoding: '{file.name}' to '{new_file.name}'{eta}"))
                    if self.run:
                        tmp = Path(file.source)
                        tmp_out = new_file.with_stem(new_file.stem)
                        if self.workspace:
                            print(COLOR.BLUE.write(f"Copying '{file.name}' to '{self.workspace}'"))
                            tmp_out = Path(self.workspace, tmp_out.name)
                            tmp = Path(self.workspace, file.name)
                            shutil.copyfile(file.source, tmp)
                        start = timeit.default_timer()
                        try:
                            subprocess.run([command, '-i', tmp, '-o', tmp_out, '--preset', self.preset, '-O'] + subtitle + audio, capture_output=True, check=True)
                        except BaseException as e:
                            if file.dest.exists():
                                file.dest.unlink()
                            if not isinstance(e, subprocess.CalledProcessError):
                                raise e
                            print(COLOR.RED.write(f'HandBrakeCLI exited with code [{e.returncode}] and stderr: {e.stderr}'))
                            continue
                        time = timeit.default_timer() - start
                        try:
                            time_avg[duration].append(time)
                        except KeyError:
                            time_avg[duration] = [time]
                        queue_data['times'].append(time)
                        queue_data['durations'].append(file.duration)
                        if self.delete_original:
                            file.source.unlink()
                        if self.workspace:
                            print(COLOR.BLUE.write(f'Copying from workspace "{tmp_out.name}" to "{new_file}"'))
                            shutil.copyfile(tmp_out, new_file)
                            tmp.unlink()
                            tmp_out.unlink()
                        print(COLOR.GREEN.write(f'Transcoded [~{time / 60:.0f} min]: {new_file.name}'))
                    else:
                        print(COLOR.GREEN.write(f'Transcoded (DRY RUN): {new_file.name}'))
                elif file.skip:
                    print(file.skip)
            pass

    @staticmethod
    def cli() -> 'Converter':
        parser = ArgumentParser(
            description='Converts all videos in nested folders to h264 and audio to aac using HandBrake with the Normal preset. This saves Plex from having to transcode files which is CPU intensive',
            epilog=textwrap.dedent('''
            Examples:
                Dry run all videos in the Movies directory
                    python convert_videos_for_plex.py -p Movies

                Transcode all videos in the current directory force overwriting matching .mp4 files.
                    python convert_videos_for_plex.py -fr

                Transcode all network videos using Desktop as temp directory and delete original files.
                    python convert_videos_for_plex.py -rd -p /Volumes/Public/Movies -w ~/Desktop'''),
            formatter_class=RawDescriptionHelpFormatter)
        parser.add_argument('-a', default='0', help='Select an audio track to use', type=int, metavar='TRACK', dest='audio_track', choices=[1, 2, 3, 4, 5])
        parser.add_argument('-b', default='0', help='Select a subtitile track to burn in', type=int, metavar='TRACK', dest='subtitle_track', choices=[1, 2, 3, 4, 5])
        parser.add_argument('-c', default='MPEG-4', help='Codec to modify [MPEG-4]', metavar='CODEC', dest='codec')
        parser.add_argument('-d', action='store_true', help='Delete original', dest='delete_original')
        parser.add_argument('-f', action='store_true', help='Force overwriting of files if already exist in output destination', dest='force')
        parser.add_argument('-o', default=None, help='Output folder directory path [Same as video]', metavar='OUTPUT', dest='output')
        parser.add_argument('-i', default='.', help='The directory path of the videos to be tidied [.]', metavar='PATH', dest='input')
        parser.add_argument('-q', default='Fast 1080p30', help='Quality of HandBrake encoding preset. List of presets: https://handbrake.fr/docs/en/latest/technical/official-presets.html [Fast 1080p30]', metavar='PRESET', dest='preset')
        parser.add_argument('-r', action='store_true', help='Run transcoding. Exclude for dry run', dest='run')
        parser.add_argument('-s', action='store_true', help='Skip transcoding if there is alread a matching filename in the output destination. Force takes precedence', dest='skip')
        parser.add_argument('-w', default=None, help='Workspace directory path for processing', dest='workspace')
        return Converter(**parser.parse_args().__dict__)


if __name__ == '__main__':
    Converter.cli().convert()
