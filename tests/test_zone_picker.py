from aion_target.zone_picker import map_selection


def test_identity_mapping():
    # Image affichée à taille réelle en (0, 36)
    assert map_selection((100, 136, 240, 32), (0, 36, 1920, 1080), (1920, 1080), (0, 0)) == [100, 100, 240, 32]


def test_scaled_down_display():
    # Image 3840x1080 (2 écrans) affichée réduite de moitié
    assert map_selection((50, 10, 120, 16), (0, 0, 1920, 540), (3840, 1080), (0, 0)) == [100, 20, 240, 32]


def test_negative_origin_multi_monitor():
    # Écran secondaire à gauche : le bureau virtuel commence à x = -1920
    assert map_selection((0, 0, 100, 20), (0, 0, 3840, 1080), (3840, 1080), (-1920, 0)) == [-1920, 0, 100, 20]


def test_selection_clamped_to_image():
    assert map_selection((-50, -50, 100, 100), (0, 0, 1000, 1000), (1000, 1000), (0, 0)) == [0, 0, 50, 50]


def test_to_widget_is_inverse_of_map_selection():
    from aion_target.zone_picker import to_widget
    shown, img, origin = (0, 36, 1280, 720), (1920, 1080), (1920, 1080)
    rect = [2722, 1104, 291, 35]                 # ta zone actuelle (écran DP-2)
    w = to_widget(rect, shown, img, origin)
    back = map_selection(tuple(w), shown, img, origin)
    assert all(abs(a - b) <= 2 for a, b in zip(back, rect))


def test_to_widget_clamps_rect_outside_shared_screen():
    from aion_target.zone_picker import to_widget
    w = to_widget([100, 100, 200, 30], (0, 0, 960, 540), (1920, 1080), (1920, 1080))
    assert 0 <= w[0] <= 960 - w[2] and 0 <= w[1] <= 540 - w[3]
