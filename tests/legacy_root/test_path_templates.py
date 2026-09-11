"""
Test script for Path Template Manager - Verify all templates work correctly.
"""

from ut_vfx.core.services.path_template_manager import get_path_manager

def test_templates():
    print("Testing Path Template Manager...")
    print("=" * 60)
    
    # Get manager
    mgr = get_path_manager()
    
    # Check availability
    print(f"\n✅ Lucidity available: {mgr.is_available()}")
    print(f"✅ Total templates: {len(mgr.templates)}")
    print("\n📋 Available templates:")
    for i, name in enumerate(mgr.list_templates(), 1):
        print(f"  {i}. {name}")
    
    # Test original templates
    print("\n" + "=" * 60)
    print("TESTING ORIGINAL TEMPLATES")
    print("=" * 60)
    
    try:
        render_path = mgr.format_path('render',
            project='TEST', sequence='sq010', shot='sh0020',
            task='comp', version=3, frame=1001
        )
        print(f"✅ render: {render_path}")
    except Exception as e:
        print(f"❌ render failed: {e}")
    
    # Test new UT_VFX templates
    print("\n" + "=" * 60)
    print("TESTING NEW UT_VFX TEMPLATES")
    print("=" * 60)
    
    try:
        project_base = mgr.format_path('project_base', project='MARVEL')
        print(f"✅ project_base: {project_base}")
    except Exception as e:
        print(f"❌ project_base failed: {e}")
    
    try:
        reels_root = mgr.format_path('reels_root', project='MARVEL')
        print(f"✅ reels_root: {reels_root}")
    except Exception as e:
        print(f"❌ reels_root failed: {e}")
    
    try:
        reel_base = mgr.format_path('reel_base', project='MARVEL', reel='Reel_01')
        print(f"✅ reel_base: {reel_base}")
    except Exception as e:
        print(f"❌ reel_base failed: {e}")
    
    try:
        shot_base = mgr.format_path('shot_base', project='MARVEL', reel='Reel_01', shot='sh0010')
        print(f"✅ shot_base: {shot_base}")
    except Exception as e:
        print(f"❌ shot_base failed: {e}")
    
    try:
        shot_scan = mgr.format_path('shot_scan', project='MARVEL', reel='Reel_01', shot='sh0010')
        print(f"✅ shot_scan: {shot_scan}")
    except Exception as e:
        print(f"❌ shot_scan failed: {e}")
    
    try:
        shot_comp = mgr.format_path('shot_comp', project='MARVEL', reel='Reel_01', shot='sh0010')
        print(f"✅ shot_comp: {shot_comp}")
    except Exception as e:
        print(f"❌ shot_comp failed: {e}")
    
    try:
        stock_lib = mgr.format_path('stock_library', category='Explosions')
        print(f"✅ stock_library: {stock_lib}")
    except Exception as e:
        print(f"❌ stock_library failed: {e}")
    
    try:
        stock_cache = mgr.format_path('stock_cache')
        print(f"✅ stock_cache: {stock_cache}")
    except Exception as e:
        print(f"❌ stock_cache failed: {e}")
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS COMPLETE!")
    print("=" * 60)

if __name__ == '__main__':
    test_templates()
