from PIL import Image
import os
import sys

def convert_images():
    base_dir = r'C:\Users\utkarsh.tripathi.SQUADVFX-26\.gemini\antigravity-ide\brain\d4409365-132d-49b4-a8c6-071c9d9ffd50'
    icon_path = os.path.join(base_dir, 'ut_vfx_app_icon_1779884867415.png')
    banner_path = os.path.join(base_dir, 'ut_vfx_installer_banner_1779884890173.png')

    out_dir = r'D:\Soft\UTCAP\V0040\ut_vfx\icons'
    os.makedirs(out_dir, exist_ok=True)

    print("Checking icon path:", icon_path, os.path.exists(icon_path))
    print("Checking banner path:", banner_path, os.path.exists(banner_path))

    try:
        # 1. Main App Icon (.ico)
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
            
            # Convert to RGBA just in case
            img = img.convert("RGBA")
            
            # Save as .ico with multiple sizes
            img.save(os.path.join(out_dir, 'app_icon.ico'), format='ICO', sizes=[(128, 128), (64,64), (32,32)])
            img.save(os.path.join(out_dir, 'app_icon_128.ico'), format='ICO', sizes=[(128, 128)])
            print('Saved app_icon.ico and app_icon_128.ico')
            
            # Save as WizardSmallImageFile (.bmp, exactly 55x58)
            small_banner = img.resize((55, 58))
            small_banner.save(os.path.join(out_dir, 'app_banner_small.bmp'))
            print('Saved app_banner_small.bmp')
            
        # 2. Installer Banner (.bmp, exactly 164x314)
        if os.path.exists(banner_path):
            banner = Image.open(banner_path)
            banner = banner.convert("RGB")
            
            # Crop center to get a vertical slice before resizing
            w, h = banner.size
            target_ratio = 164 / 314
            current_ratio = w / h
            
            if current_ratio > target_ratio:
                # Too wide, crop width
                new_w = int(h * target_ratio)
                left = (w - new_w) / 2
                banner = banner.crop((left, 0, left + new_w, h))
            else:
                # Too tall, crop height
                new_h = int(w / target_ratio)
                top = (h - new_h) / 2
                banner = banner.crop((0, top, w, top + new_h))
                
            final_banner = banner.resize((164, 314))
            final_banner.save(os.path.join(out_dir, 'app_banner.bmp'))
            print('Saved app_banner.bmp')
    except Exception as e:
        print("Error during conversion:", str(e))

if __name__ == "__main__":
    convert_images()
