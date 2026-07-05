# Hyper-V-Dashboard
A Python tool that uses PowerShell to pull VMs and allows graphical visualization of live data and dataflows, including zones, comments and images.

## Requirements
``pip install -r requirements.txt``

## Running the app
- Ensure you are running with admin privileges to pull HyperV data
``python hypervd.py``

## Creating executable
``python -m PyInstaller --onefile hypervd.py`` - requires pyinstaller
