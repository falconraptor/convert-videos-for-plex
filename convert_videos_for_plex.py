import math
import os
import shutil
import subprocess
import sys
import timeit
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from copy import copy
from enum import Enum
from io import BytesIO
from pathlib import Path
import textwrap
from statistics import mean
from zipfile import ZipFile

import requests as requests
from pymediainfo import MediaInfo


class COLOR(str, Enum):
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

    def write(self, string: str) -> str:
        return f'{self.value}{string}{self.NC}'


class LockFile:
    def __init__(self, file: Path):
        self.lock_file = file.with_suffix('.lock')

    def __enter__(self) -> 'LockFile':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file.exists():
            self.lock_file.unlink()

    def exists(self) -> bool:
        return self.lock_file.exists()

    def touch(self):
        self.lock_file.touch()


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

    def get_files(self) -> list[Path]:
        files = []
        for ext in ('avi', 'mkv', 'iso', 'img', 'mp4', 'm4v', 'ts'):
            files.extend(_.resolve() for _ in self.input.glob(f'**/*.{ext}'))
        print(COLOR.GREEN.write(f'{len(files)}'))
        files.sort()
        return files

    @staticmethod
    def get_command():
        command = 'HandBrakeCLI'
        if sys.platform == 'win32':
            command += '.exe'
            for path in [Path('.').resolve()] + os.path.expandvars('$PATH').split(';'):
                if Path(path, command).exists():
                    break
            else:
                print(COLOR.RED.write('HandBrakeCLI.exe not found, downloading'))
                version = requests.get('https://github.com/HandBrake/HandBrake/releases/latest').url.split('/')[-1]
                ZipFile(BytesIO(requests.get(f'https://github.com/HandBrake/HandBrake/releases/download/{version}/HandBrakeCLI-{version}-win-x86_64.zip').content)).extract('HandBrakeCLI.exe')
        else:
            for path in os.path.expandvars('$PATH').split(';'):
                if Path(path, command).exists():
                    break
            else:
                print(COLOR.RED.write('HandBrakeCLI is not installed, please install it using the instructions in the README.md'))
                exit(127)
        return command

    def convert(self):
        audio = ['--audio', self.audio_track] if self.audio_track else ['--audio-lang-list', 'und', '--all-audio']
        subtitle = ['--subtitle', self.subtitle_track, '--subtitle_burned'] if self.subtitle_track else ['-s', 'scan']
        files = self.get_files()
        count = len(files)
        count_len = len(str(count))
        time_avg = {}
        command = self.get_command()
        times = []
        for i, file in enumerate(files):
            with LockFile(file) as lock:
                if lock.exists():
                    print(COLOR.RED.write(f"Lockfile for '{file.name}' exists, skipping"))
                    continue
                new_file = file.with_suffix('.mp4')
                if self.output:
                    new_file = Path(self.output, new_file.name)
                if self.run:
                    lock.touch()
                i += 1
                eta = ''
                if len(times) >= 2:
                    eta = f' [Queue ETA: ~{mean(times) / 60 * (count - i + 1):.0f} min]'
                print(COLOR.BLUE.write(f"Checking [{i.__str__().rjust(count_len, '0')}/{count} ({i/count:.0%})]: '{file.name}'{eta}"))
                try:
                    media_info = MediaInfo.parse(file)
                except FileNotFoundError:
                    print(COLOR.RED.write(f"Skipping (not found): '{file.name}'"))
                    continue
                if not media_info.video_tracks:
                    print(COLOR.RED.write(f"Skipping (missing info): '{file.name}'"))
                    continue
                format = media_info.video_tracks[0].format
                profile = media_info.video_tracks[0].format_profile
                duration = math.ceil(float(media_info.video_tracks[0].duration) / 600000) * 10
                if self.audio_track or self.subtitle_track or format in ('HEVC', 'xvid', 'MPEG Video') or self.codec in format or (format == 'AVC' and '@L5' in profile):
                    if new_file.exists():
                        if self.force:
                            print(COLOR.RED.write(f"Overwriting: '{new_file.name}'"))
                        else:
                            if self.skip:
                                print(COLOR.RED.write(f"Skipping (already exists): '{new_file.name}'"))
                                continue
                            else:
                                while reply := input(f"'{new_file.name}' already exists, do you wish to overwrite it [y|n]? ").lower() in ('y', 'n'):
                                    pass
                                if reply == 'y':
                                    print(COLOR.RED.write(f"Overwriting: '{new_file.name}'"))
                                elif reply == 'n':
                                    print(COLOR.RED.write(f"Skipping (already exists): '{new_file.name}'"))
                                    continue
                    eta = ''
                    if len(time_avg.get(duration, [])) >= 2:
                        eta = f' [ETA: ~{mean(time_avg[duration]) / 60:.0f} min]'
                    print(COLOR.BLUE.write(f"Transcoding: '{file.name}' to '{new_file.name}'{eta}"))
                    if self.run:
                        tmp = Path(file)
                        tmp_out = new_file.with_name(f'{new_file.stem}_processing.mp4')
                        if self.workspace:
                            print(COLOR.BLUE.write(f"Copying '{file.name}' to '{self.workspace}'"))
                            tmp_out = Path(self.workspace, new_file.name)
                            tmp = Path(self.workspace, file.name)
                            shutil.copyfile(file, tmp)
                        start = timeit.default_timer()
                        handbrake = subprocess.run([command, '-i', tmp, '-o', tmp_out, '--preset', self.preset, '-O'] + subtitle + audio, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if handbrake.returncode != 0:
                            print(COLOR.RED.write(f'HandBrakeCLI exited with code: {handbrake.returncode}'))
                            continue
                        time = timeit.default_timer() - start
                        try:
                            time_avg[duration].append(time)
                        except KeyError:
                            time_avg[duration] = [time]
                        times.append(time)
                        if self.delete_original:
                            file.unlink()
                        tmp_out = tmp_out.rename(tmp_out.with_stem(f'{new_file.stem}.mp4'))
                        if self.workspace:
                            print(COLOR.BLUE.write(f'Copying from workspace "{tmp_out.name}" to "{new_file}"'))
                            shutil.copyfile(tmp_out, new_file)
                            tmp.unlink()
                            tmp_out.unlink()
                        print(COLOR.GREEN.write(f'Transcoded: {new_file.name}'))
                    else:
                        print(COLOR.GREEN.write(f'Transcoded (DRY RUN): {new_file.name}'))
                else:
                    print(COLOR.RED.write(f'Skipping (video format {format} {profile} will already play in Plex)'))

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
