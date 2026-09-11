import subprocess
import json
import platform
import socket
import re
import shutil
import uuid
import os
import logging
from typing import Dict, Any

class HardwareInfo:
    @staticmethod
    def _run_cmd(command: str, timeout=2) -> str:
        """Run command with timeout to prevent hangs."""
        try:
            # Hide console window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            result = subprocess.run(
                command, 
                capture_output=True, 
                text=True, 
                shell=True, 
                timeout=timeout,
                startupinfo=startupinfo
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def get_static_specs() -> Dict[str, Any]:
        specs = {}
        
        # 1. Basic Identity (Fast Python calls)
        specs['ComputerName'] = socket.gethostname()
        try:
            specs['IPAddress'] = socket.gethostbyname(specs['ComputerName'])
        except Exception:
            specs['IPAddress'] = "Unknown"
            
        try:
            specs['MACAddress'] = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
        except Exception:
            specs['MACAddress'] = "Unknown"

        # 2. Operating System (Fast Python calls)
        specs['OS'] = f"{platform.system()} {platform.release()}"
        specs['WindowsVersion'] = platform.version()
        try:
            specs['os_user'] = os.getlogin()
        except Exception:
            specs['os_user'] = "Unknown"

        # 3. CPU (Registry - Most Reliable for Windows 10/11)
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            cpu_name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            specs['CPU'] = cpu_name.strip()
            winreg.CloseKey(key)
        except Exception:
            # Fallback: PowerShell
            try:
                cpu_cmd = 'powershell "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name"'
                cpu_name = HardwareInfo._run_cmd(cpu_cmd)
                specs['CPU'] = cpu_name if cpu_name else (platform.processor() or "Unknown")
            except Exception:
                specs['CPU'] = platform.processor() or "Unknown"

        # 4. Heavy Hardware (PowerShell - More reliable than WMIC)
        # GPU with VRAM
        try:
            gpu_cmd = 'powershell "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json"'
            gpu_json = HardwareInfo._run_cmd(gpu_cmd)
            # Handle single object or array
            if gpu_json:
                gpus = json.loads(gpu_json)
                if isinstance(gpus, dict): gpus = [gpus]
                
                gpu_list = []
                for g in gpus:
                    name = g.get('Name', 'Unknown')
                    vram = g.get('AdapterRAM')
                    if vram and isinstance(vram, int):
                        vram_gb = round(vram / (1024**3), 1)
                        if vram_gb > 0:
                            name += f" ({vram_gb} GB)"
                    gpu_list.append(name)
                specs['GPU'] = " | ".join(gpu_list)
            else:
                specs['GPU'] = "Unknown"
        except Exception:
            specs['GPU'] = "Unknown"

        # RAM
        ram_cmd = 'powershell "Get-CimInstance Win32_ComputerSystem | Measure-Object -Property TotalPhysicalMemory -Sum | ForEach-Object { [Math]::Round($_.Sum / 1GB, 1) }"'
        ram = HardwareInfo._run_cmd(ram_cmd)
        specs['RAM_GB'] = f"{ram} GB" if ram else "Unknown"
        
        # Motherboard (BaseBoard)
        try:
            mb_cmd = 'powershell "Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product | ConvertTo-Json"'
            mb_json = HardwareInfo._run_cmd(mb_cmd)
            if mb_json:
                mb = json.loads(mb_json)
                # If array, take first
                if isinstance(mb, list): mb = mb[0]
                
                man = mb.get('Manufacturer', '').strip()
                prod = mb.get('Product', '').strip()
                specs['Motherboard'] = f"{man} {prod}"
            else:
                specs['Motherboard'] = "Unknown"
        except Exception:
            specs['Motherboard'] = "Unknown"
        
        # System Model (Chassis)
        model_cmd = 'powershell "Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Model"'
        specs['Model'] = HardwareInfo._run_cmd(model_cmd) or "Unknown"

        man_cmd = 'powershell "Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer"'
        specs['Manufacturer'] = HardwareInfo._run_cmd(man_cmd) or "Unknown"
        
        serial_cmd = 'powershell "Get-CimInstance Win32_Bios | Select-Object -ExpandProperty SerialNumber"'
        specs['SerialNo'] = HardwareInfo._run_cmd(serial_cmd) or "Unknown"

        return specs

    @staticmethod
    def get_dynamic_specs() -> Dict[str, Any]:
        """Get drive info using Python standard library (Fast/Safe)."""
        specs = {}
        drives = []
        
        # Get logical drives
        try:
            # win32 api approach or standard logical drive check
            import string
            available_drives = ['%s:' % d for d in string.ascii_uppercase if os.path.exists('%s:' % d)]
            
            for drive_letter in available_drives:
                try:
                    usage = shutil.disk_usage(drive_letter)
                    total_gb = round(usage.total / (1024**3), 1)
                    free_gb = round(usage.free / (1024**3), 1)
                    used_percent = round((usage.used / usage.total) * 100, 1)
                    
                    drives.append({
                        "Root": drive_letter,
                        "Label": "Local Disk", # Python specific label getting is hard without win32api
                        "Capacity_GB": total_gb,
                        "Free_GB": free_gb,
                        "Usage": f"{used_percent}%"
                    })
                except (OSError, ValueError):
                    continue
        except Exception as exc:
            logging.debug("Drive discovery failed in get_dynamic_specs: %s", exc)
            
        specs['Drives'] = drives
        return specs
