@echo off
d:
cd D:\_Devices\Heltec\_Meshcore\MeshCoreBotPy\
chcp.com 65001
call D:\_Devices\Heltec\_Meshcore\MeshCoreBotPy\.venv\Scripts\activate.bat
start /b "" pythonw.exe D:\_Devices\Heltec\_Meshcore\MeshCoreBotPy\bot.py > stdout.txt 2> stderr.txt