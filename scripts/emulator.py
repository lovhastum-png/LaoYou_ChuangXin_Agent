"""Create and run the acceptance emulator with all writable data in this workspace."""
from pathlib import Path
import os
import subprocess
import sys
from database import tool_root

ROOT = tool_root()
SDK = ROOT / 'runtime/android-sdk'
TEMP = ROOT / 'tmp'
AVDS = TEMP / 'android-avd'
ENV = os.environ.copy()
ENV.update(ANDROID_HOME=str(SDK), ANDROID_SDK_ROOT=str(SDK),
           ANDROID_USER_HOME=str(TEMP/'android-user'), ANDROID_AVD_HOME=str(AVDS),
           ANDROID_EMULATOR_HOME=str(TEMP/'android-user'), ANDROID_TMP=str(TEMP/'android-emulator'),
           JAVA_HOME='C:/Program Files/Java/jdk-17', TEMP=str(TEMP), TMP=str(TEMP))
NAME = 'Laoyou_API35'

def main():
    AVDS.mkdir(parents=True, exist_ok=True)
    (TEMP/'android-emulator').mkdir(exist_ok=True)
    tools_location = SDK/'cmdline-tools/latest'
    tools_location.mkdir(parents=True, exist_ok=True)
    if not (AVDS / f'{NAME}.ini').exists():
        result = subprocess.run(['C:/Program Files/Java/jdk-17/bin/java.exe',
                                 '-Dcom.android.sdkmanager.toolsdir='+str(tools_location),
                                 '-classpath', 'D:/Android/Sdk/cmdline-tools/latest/lib/avdmanager-classpath.jar',
                                 'com.android.sdklib.tool.AvdManagerCli',
                                 'create', 'avd', '-n', NAME, '-k', 'system-images;android-35;google_apis;x86_64',
                                 '-p', str(AVDS/f'{NAME}.avd')], input='no\n', text=True, env=ENV,
                                capture_output=True, encoding='utf-8', errors='replace')
        if result.returncode:
            print(result.stdout, result.stderr)
            return result.returncode
    ini = AVDS / f'{NAME}.ini'
    lines = ini.read_text('utf-8').splitlines()
    lines = ['path=' + (AVDS/f'{NAME}.avd').as_posix() if line.startswith('path=') else line for line in lines]
    ini.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    config = AVDS/f'{NAME}.avd/config.ini'
    settings = dict(line.split('=', 1) for line in config.read_text('utf-8').splitlines() if '=' in line)
    settings.update({'hw.lcd.width': '1080', 'hw.lcd.height': '1920', 'hw.lcd.density': '420',
                     'hw.camera.front': 'emulated', 'hw.camera.back': 'emulated'})
    config.write_text(''.join(f'{key}={value}\n' for key,value in settings.items()), encoding='utf-8')
    log = (TEMP/'emulator.log').open('a', encoding='utf-8')
    args = [str(SDK/'emulator/emulator.exe'), '-avd', NAME, '-no-window',
            '-no-boot-anim', '-no-snapshot', '-no-metrics', '-ports', '5554,5555',
            '-camera-front', 'emulated', '-camera-back', 'emulated',
            '-gpu', 'swiftshader', '-memory', '2048', '-cores', '2']
    process = subprocess.Popen(args, env=ENV, cwd=ROOT, stdout=log, stderr=log,
                               stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    print(f'Headless acceptance emulator PID {process.pid}; log: {TEMP / "emulator.log"}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
