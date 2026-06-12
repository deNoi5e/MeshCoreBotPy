#!/usr/bin/env python3
"""
Скрипт для поиска доступных USB портов
Помогает найти правильный порт устройства
"""

import sys
import subprocess
from pathlib import Path


def find_usb_ports_macos():
    """Найти USB порты на macOS"""
    print("🔍 Поиск USB портов на macOS...\n")

    try:
        result = subprocess.run(
            ["ls", "-la", "/dev/tty.usbmodem*"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            ports = result.stdout.strip().split("\n")
            print("📱 Найденные USB порты:")
            for port in ports:
                if port:
                    parts = port.split()
                    device_path = parts[-1]
                    print(f"  • {device_path}")
            return ports
        else:
            print("❌ Никакие USB устройства не найдены")
            print("\nПроверьте:")
            print("  1. USB кабель подключен")
            print("  2. Устройство включено")
            print("  3. Устройство находится в режиме компаньона")
            return []

    except FileNotFoundError:
        print("❌ Команда 'ls' не найдена")
        return []


def find_usb_ports_linux():
    """Найти USB порты на Linux"""
    print("🔍 Поиск USB портов на Linux...\n")

    try:
        result = subprocess.run(
            ["ls", "-la", "/dev/ttyUSB*", "/dev/ttyACM*"],
            capture_output=True,
            text=True,
            shell=True
        )

        if result.returncode == 0 or result.stdout:
            ports = result.stdout.strip().split("\n")
            print("📱 Найденные USB порты:")
            for port in ports:
                if port:
                    parts = port.split()
                    device_path = parts[-1]
                    print(f"  • {device_path}")
            return ports
        else:
            print("❌ Никакие USB устройства не найдены")
            return []

    except FileNotFoundError:
        print("❌ Команда 'ls' не найдена")
        return []


def find_usb_ports_windows():
    """Найти COM порты на Windows"""
    print("🔍 Поиск COM портов на Windows...\n")

    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()

        if ports:
            print("📱 Найденные COM порты:")
            for port in ports:
                print(f"  • {port.device} - {port.description}")
            return [port.device for port in ports]
        else:
            print("❌ Никакие COM устройства не найдены")
            return []

    except ImportError:
        print("❌ pyserial не установлен")
        print("Установите: pip install pyserial")
        return []
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return []


def find_device_ports():
    """Найти доступные USB порты в зависимости от ОС"""
    system = sys.platform

    if system == "darwin":
        ports = find_usb_ports_macos()
    elif system.startswith("linux"):
        ports = find_usb_ports_linux()
    elif system == "win32":
        ports = find_usb_ports_windows()
    else:
        print(f"❌ Неизвестная ОС: {system}")
        return []

    return ports


def update_env(port):
    """Обновить .env с найденным портом"""
    env_path = Path(".env")
    try:
        lines = env_path.read_text().splitlines(keepends=True) if env_path.exists() else []
        found = False
        for i, line in enumerate(lines):
            if line.startswith("MESHCORE_PORT="):
                lines[i] = f"MESHCORE_PORT={port}\n"
                found = True
                break
        if not found:
            lines.append(f"MESHCORE_PORT={port}\n")
        env_path.write_text("".join(lines))
        print(f"\n✅ .env обновлён с портом: {port}")
        return True
    except Exception as e:
        print(f"❌ Ошибка при обновлении .env: {e}")
        return False


def main():
    print("=" * 50)
    print("  Поиск USB устройства для MeshCore Bot")
    print("=" * 50 + "\n")

    ports = find_device_ports()

    if not ports:
        print("\n💡 Убедитесь, что устройство подключено и включено")
        sys.exit(1)

    if len(ports) == 1:
        port = ports[0]
        print(f"\n✨ Найден один портал: {port}")
        if update_env(port):
            print("\n🎉 Готово! Запустите: python bot.py")
    else:
        print("\n⚠️  Найдено несколько портов. Выберите нужный:")
        for i, port in enumerate(ports, 1):
            print(f"  {i}. {port}")

        try:
            choice = input("\nВведите номер (1-{}): ".format(len(ports)))
            idx = int(choice) - 1

            if 0 <= idx < len(ports):
                port = ports[idx]
                if update_env(port):
                    print("\n🎉 Готово! Запустите: python bot.py")
            else:
                print("❌ Неверный выбор")
                sys.exit(1)
        except ValueError:
            print("❌ Введите число")
            sys.exit(1)


if __name__ == "__main__":
    main()
