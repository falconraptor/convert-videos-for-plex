import math
import os
import shutil
import subprocess
import sys
import timeit
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from datetime import datetime
from enum import Enum
from io import BytesIO
from pathlib import Path
import textwrap
from statistics import mean
from typing import Iterable
from zipfile import ZipFile

import requests
from pymediainfo import MediaInfo

MILLISEC_TO_MIN = 60000
FATAL_ERROR = 127


class COLOR(str, Enum):
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

    def write(self, string: str) -> str:
        return f'{self.value}{datetime.now().strftime("%b %d %H:%M")}\t{string}{self.NC.value}'


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

    def __repr__(self):
        return f'<LockFile {self.lock_file=} {self._touched=}>'


class File:
    def __init__(self, source: Path, converter: 'Converter'):
        self.source = source
        self.dest = source.with_suffix('.mp4')
        self.converter = converter
        if self.converter.output:
            self.dest = Path(self.converter.output, self.dest.name)
        self.skip = ''
        self.run = False
        self.media_info = None
        self.duration_min = 0.0
        self.duration = 0
        self.format = ''
        self.profile = ''

    def check_output_exists(self) -> 'File':
        if self.skip:
            return self
        if self.dest.exists():
            if self.converter.force:
                self.skip = COLOR.RED.write(f"Overwriting: '{self.dest.name}'")
            else:
                self.skip = COLOR.RED.write(f"Skipping (already exists): '{self.dest.name}'")
        return self

    def get_duration(self) -> float:
        if self.duration:
            return self.duration
        if not self.media_info:
            self.media_info = MediaInfo.parse(self.source)
        self.duration = float(self.media_info.video_tracks[0].duration or 0) / MILLISEC_TO_MIN
        self.duration_min = math.ceil(self.duration / 10) * 10
        return self.duration

    def check_media_info(self, preset: str) -> 'File':
        if self.skip:
            return self
        if not self.media_info:
            self.media_info = MediaInfo.parse(self.source)
        if not self.media_info.video_tracks:
            self.skip = COLOR.RED.write(f"Skipping (missing info): '{self.name}'")
            return self
        self.format = self.media_info.video_tracks[0].format
        self.profile = self.media_info.video_tracks[0].format_profile
        if not self.duration_min:
            self.get_duration()
        match preset, self.format:
            case 'H.265 VCN 1080p', 'HEVC':
                pass
            case 'Fast 1080p30', 'AVC':
                pass
            case _, _:
                self.run = True
        if not self.run:
            self.skip = COLOR.RED.write(f'Skipping (video format {self.format} {self.profile} already requested)')
        return self

    def __lt__(self, other) -> bool:
        return self.source.__str__() < other.source.__str__()

    @property
    def name(self) -> str:
        return self.source.name

    def __repr__(self):
        return f'<File {self.source=}>'


class Converter:
    def __init__(self, input: str = '.', output: str = None, workspace: str = None, run: bool = False, delete_original: bool = False, force: bool = False, audio_track: int = 0, subtitle_track: int = 0, preset: str = 'Fast 1080p30', sort_type: str = 'Name', sort_direction: str = 'ASC', extensions: Iterable[str] = None, exclude: Iterable[str] = None, stop_larger: bool = False):
        self.input = Path(input).resolve()
        self.output = Path(output).resolve() if output else None
        self.workspace = Path(workspace).resolve() if workspace else None
        self.run = run
        self.delete_original = delete_original
        self.force = force
        self.audio_track = audio_track
        self.subtitle_track = subtitle_track
        self.preset = preset
        self.sort_type = sort_type
        self.sort_direction = sort_direction
        if not extensions:
            extensions = ('avi', 'mkv', 'iso', 'img', 'mp4', 'm4v', 'ts')
        self.extensions = extensions
        if not exclude:
            exclude = []
        self.exclude = exclude
        self.stop_larger = stop_larger
        print(COLOR.BLUE.write("TRANSCODING" if self.run else "DRY RUN"))

    def get_files(self) -> list[File]:
        files = []
        exts = ('avi', 'mkv', 'iso', 'img', 'm4v', 'ts')
        exts_len = len(exts)
        for i, ext in enumerate(exts):
            print(COLOR.BLUE.write(f'Finding files step {i + 1} of {exts_len} [{ext}]: '), end='', flush=True)
            amount = len(files)
            skipping = 0
            for source in self.input.glob(f'**/*.{ext}'):
                file = File(source, self).check_output_exists()
                # if not file.skip:
                #     file.check_media_info(self.preset)
                if not self.force and file.skip:
                    skipping += 1
                    continue
                files.append(file)
            print(COLOR.GREEN.write(f'Found {len(files) - amount}') + (COLOR.RED.write(f'\tSkipping {skipping}') if skipping else ''))
        print(COLOR.GREEN.write(f'Total: {len(files)}'))
        match self.sort_type:
            case 'Name':
                return sorted(files, key=lambda f: f.name, reverse=self.sort_direction == 'DESC')
            case 'Duration':
                return sorted(files, key=lambda f: f.get_duration(), reverse=self.sort_direction == 'DESC')
            case 'Filesize':
                return sorted(files, key=lambda f: f.source.stat().st_size, reverse=self.sort_direction == 'DESC')
            case 'Modified':
                return sorted(files, key=lambda f: f.source.stat().st_mtime, reverse=self.sort_direction == 'DESC')
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

    @staticmethod
    def calc_time(seconds: int | float):
        seconds = int(seconds)
        minutes = seconds // 60
        hours = seconds // 3600
        return f'{hours:02}H {minutes:02}M'

    def convert(self):
        audio = ['--audio', self.audio_track] if self.audio_track != 0 else ['--all-audio']
        subtitle = ['--subtitle', self.subtitle_track, '--subtitle_burned'] if self.subtitle_track != 0 else ['-s', 'scan']
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
                    eta = f' [Queue ETA: ~{self.calc_time(avg * (count - i))}]'
                i += 1
                print(COLOR.BLUE.write(f"Checking [{i:0{count_len}}/{count} ({i/count:.0%})]: '{file.name}'{eta}"))
                file.check_output_exists()
                if file.skip and not self.force:
                    print(file.skip)
                    continue
                new_file = file.dest
                try:
                    file.check_media_info(self.preset)
                except RuntimeError as e:
                    print(COLOR.RED.write(f'ERROR: {e.__repr__()}'))
                    continue
                if file.run:
                    eta = ''
                    duration = file.duration_min
                    if len(time_avg.get(duration, [])) >= 2:
                        eta = f' [ETA: ~{self.calc_time(mean(time_avg[duration]))}]'
                    print(COLOR.BLUE.write(f"Transcoding: '{file.name}' to '{new_file.name}'{eta}"))
                    if self.run:
                        tmp = Path(file.source)
                        tmp_out = new_file.with_stem(new_file.stem)
                        if self.workspace:
                            print(COLOR.BLUE.write(f"Copying '{file.name}' to '{self.workspace}'"))
                            tmp_out = Path(self.workspace, tmp_out.name)
                            tmp = Path(self.workspace, file.name)
                            shutil.copy2(file.source, tmp)
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
                        if self.workspace:
                            print(COLOR.BLUE.write(f'Copying from workspace "{tmp_out.name}" to "{new_file}"'))
                            shutil.copy2(tmp_out, new_file)
                            tmp.unlink()
                            tmp_out.unlink()
                        original_size = file.source.stat().st_size
                        new_size = new_file.stat().st_size
                        print(COLOR.GREEN.write(f'Transcoded [~{self.calc_time(time)}]: {new_file.name} [{new_size / original_size:03.2%}]'))
                        if self.stop_larger and new_size > original_size:
                            print(COLOR.RED.write('Output > Input: STOPPING'))
                            file.dest.unlink()
                            break
                        if self.delete_original:
                            file.source.unlink()
                    else:
                        print(COLOR.GREEN.write(f'Transcoded (DRY RUN): {new_file.name}'))
                elif file.skip:
                    print(file.skip)

    @staticmethod
    def cli() -> 'Converter':
        parser = ArgumentParser(
            description='Converts all videos in nested folders to h264 and audio to aac using HandBrake with the Normal preset. This saves Plex from having to transcode files which is CPU intensive',
            epilog=textwrap.dedent('''
            Examples:
                Dry run all videos in the Movies directory
                    python convert_videos_for_plex.py -i Movies

                Transcode all videos in the current directory force overwriting matching .mp4 files.
                    python convert_videos_for_plex.py -fr

                Transcode all network videos using Desktop as temp directory and delete original files.
                    python convert_videos_for_plex.py -rd -i /Volumes/Public/Movies -w ~/Desktop'''),
            formatter_class=RawDescriptionHelpFormatter)
        parser.add_argument('-a', '--audio_track', default='0', help='Select an audio track to use', type=int, metavar='TRACK', dest='audio_track', choices=[1, 2, 3, 4, 5])
        parser.add_argument('-s', '--subtitle_track', default='0', help='Select a subtitle track to burn in', type=int, metavar='TRACK', dest='subtitle_track', choices=[1, 2, 3, 4, 5])
        parser.add_argument('-d', '--delete_original', action='store_true', help='Delete original', dest='delete_original')
        parser.add_argument('-o', '--output', default=None, help='Output folder directory path [Same as video]', metavar='OUTPUT', dest='output')
        parser.add_argument('-i', '--input', default='.', help='The directory path of the videos to be tidied [.]', metavar='PATH', dest='input')
        parser.add_argument('-p', '--preset', default='Fast 1080p30', help='Quality of HandBrake encoding preset. List of presets: https://handbrake.fr/docs/en/latest/technical/official-presets.html [Fast 1080p30]', metavar='PRESET', dest='preset')
        parser.add_argument('-r', '--run', action='store_true', help='Run transcoding. Exclude for dry run', dest='run')
        parser.add_argument('-w', '--workspace', default=None, help='Workspace directory path for processing', dest='workspace')
        parser.add_argument('--sort_type', default='Name', help='Run in sort order [Name]', choices=['Name', 'Duration', 'Filesize', 'Modified'], dest='sort_type')
        parser.add_argument('--sort_direction', default='DESC', help='Sort direction [DESC]', choices=['ASC', 'DESC'], dest='sort_direction')
        parser.add_argument('-e', '--extensions', help='File extensions to check [avi, mkv, iso, img, m4v, ts]', action='extend', dest='extensions')
        parser.add_argument('--exclude', help='Files or directories to exclude (regex)', action='extend', dest='exclude', metavar='FILE_DIR_REGEX')
        parser.add_argument('-f', action='store_true', help='Force overwriting of files if already exist in output destination', dest='force')
        parser.add_argument('--stop_larger', help='Quit if output is larger than input (should only use if sort_type=Filesize)', action='store_true', dest='stop_larger')
        return Converter(**parser.parse_args().__dict__)


if __name__ == '__main__':
    Converter.cli().convert()
