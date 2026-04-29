import pytest
from agent.action_parser import (
    round_by_factor, floor_by_factor, ceil_by_factor,
    smart_resize_v15, normalize_box, parse_action_string, parse_actions
)

# ═══════════════════════════════════════════════════════════════════
# Simplified Mutation Test Suite (35 Tests)
# ═══════════════════════════════════════════════════════════════════

class TestCoordinateMath:
    """Tests for rounding, floor, and ceil logic (12 Tests)."""
    
    def test_round_normal(self): assert round_by_factor(12.7, 5) == 15
    def test_round_down(self): assert round_by_factor(12.2, 5) == 10
    def test_round_boundary(self): assert round_by_factor(2.6, 5) == 5
    def test_round_zero(self): assert round_by_factor(0, 10) == 0

    def test_floor_normal(self): assert floor_by_factor(14.9, 5) == 10
    def test_floor_boundary(self): assert floor_by_factor(5.0, 5) == 5
    def test_floor_negative(self): assert floor_by_factor(-1.1, 5) == -5
    def test_floor_zero(self): assert floor_by_factor(0, 10) == 0

    def test_ceil_normal(self): assert ceil_by_factor(11.1, 5) == 15
    def test_ceil_boundary(self): assert ceil_by_factor(10.0, 5) == 10
    def test_ceil_negative(self): assert ceil_by_factor(-4.9, 5) == 0
    def test_ceil_zero(self): assert ceil_by_factor(0, 10) == 0

class TestBoxLogic:
    """Tests for resizing and normalization (10 Tests)."""

    def test_resize_normal(self):
        assert smart_resize_v15(1000, 1000) == (992, 992)
    
    def test_resize_min(self):
        res = smart_resize_v15(100, 100)
        assert res == (256, 256)

    def test_resize_max_pixels(self):
        # Triggers lines 40-42: floor scaling
        res = smart_resize_v15(3000, 3000)
        assert res == (1344, 1344)

    def test_resize_aspect_ratio(self):
        assert smart_resize_v15(100, 2000) is None

    def test_resize_zero(self):
        assert smart_resize_v15(0, 500) is None

    def test_norm_basic(self):
        assert normalize_box([50, 50], (100, 100)) == [0.5, 0.5, 0.5, 0.5]

    def test_norm_box(self):
        assert normalize_box([10, 20, 30, 40], (100, 200)) == [0.1, 0.1, 0.3, 0.2]

    def test_norm_with_resize(self):
        assert normalize_box([10, 10], (100, 100), (200, 200)) == [0.05, 0.05, 0.05, 0.05]

    def test_norm_float_input(self):
        assert normalize_box([10.5, 20.5], (100, 100)) == [0.105, 0.205, 0.105, 0.205]
    
    def test_resize_fallback(self):
        # Triggers line 160-162 (None fallback)
        res = parse_actions("click(point=[0,0,0,0])", screen_context={"width": 0, "height": 100}, model_v15=True)
        assert res[0]["error"] is False

class TestStringParsing:
    """Tests for regex parsing (13 Tests)."""

    def test_parse_basic(self):
        res = parse_action_string("click(point=[500,500])")
        assert res["function"] == "click"
        assert res["args"]["start_box"] == "[500,500]"

    def test_parse_with_aliases(self):
        res = parse_action_string("click(start_point=[0,0])")
        assert res["args"]["start_box"] == "[0,0]"

    def test_parse_markup_bbox(self):
        # Triggers lines 117-118
        res = parse_action_string("click(point=<bbox>10 20</bbox>)")
        assert res["args"]["start_box"] == "(10,20)"

    def test_parse_markup_point(self):
        # Triggers lines 122-123
        res = parse_action_string("click(point=<point>5 5</point>)")
        assert res["args"]["start_box"] == "(5,5)"

    def test_parse_type(self):
        res = parse_action_string("type(text='hello')")
        assert res["args"]["text"] == "hello"

    def test_parse_drag(self):
        res = parse_action_string("drag(start_point=[0,0], end_point=[1,1])")
        assert res["args"]["start_box"] == "[0,0]"
        assert res["args"]["end_box"] == "[1,1]"

    def test_parse_wait(self):
        assert parse_action_string("wait()")["function"] == "wait"

    def test_parse_complex_text(self):
        res = parse_action_string("type(text='User: Hello!')")
        assert res["args"]["text"] == "User: Hello!"

    def test_parse_malformed(self):
        assert parse_action_string("not_an_action") is None

    def test_full_parse_click(self):
        ctx = {"width": 1000, "height": 1000}
        res = parse_actions("click(point=[500,500,500,500])", screen_context=ctx)
        assert res[0]["action_type"] == "click"
        assert res[0]["action_inputs"]["start_coords"] == [500, 500]

    def test_full_parse_drag_coords(self):
        # Triggers lines 224-235 (drag end_coords)
        ctx = {"width": 1000, "height": 1000}
        res = parse_actions("drag(start_point=[0,0,0,0], end_point=[100,100,100,100])", screen_context=ctx)
        assert res[0]["action_inputs"]["end_coords"] == [100, 100]

    def test_full_parse_type(self):
        res = parse_actions("type(text='test')")
        assert res[0]["action_inputs"]["text"] == "test"

    def test_list_multiple_actions(self):
        actions = parse_actions("click(point=[1,1,1,1])\n\ntype(text='hi')")
        assert len(actions) == 2

    # --- NEW V2 KILLER TESTS ---
    def test_parse_action_prefix(self):
        res = parse_actions("Action: click(point=[0,0,0,0])")
        assert res[0]["action_type"] == "click"

    def test_parse_sentinel_stripping(self):
        res = parse_action_string("<|box_start|>click(point=[0,0])<|box_end|>")
        assert res["function"] == "click"

    def test_parse_input_value(self):
        res = parse_actions("input(value='xyz')")
        assert res[0]["action_inputs"]["text"] == "xyz"
        assert res[0]["action_type"] == "input"

    def test_invalid_start_length(self):
        # Triggers the length < 4 logger warning branch
        res = parse_actions("click(point=[10,20,30])", screen_context={"width": 100, "height": 100})
        assert "start_coords" not in res[0]["action_inputs"]

    def test_invalid_end_length(self):
        # Triggers the length < 4 logger warning branch for drag
        res = parse_actions("drag(start_point=[0,0], end_point=[10,20,30])", screen_context={"width": 100, "height": 100})
        assert "end_coords" not in res[0]["action_inputs"]

