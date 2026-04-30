import pytest
from agent.action_parser import (
    round_by_factor, floor_by_factor, ceil_by_factor,
    smart_resize_v15, normalize_box, parse_action_string, parse_actions
)

# =====================================================================
# Simple Tests for LocalCUA Action Parser
# =====================================================================

class TestBasicMathHelpers:
    """Tests to make sure numbers round correctly."""
    
    def test_round_numbers_normally(self): assert round_by_factor(12.7, 5) == 15
    def test_round_numbers_down(self): assert round_by_factor(12.2, 5) == 10
    def test_round_exact_numbers(self): assert round_by_factor(2.6, 5) == 5
    def test_round_zero(self): assert round_by_factor(0, 10) == 0

    def test_floor_numbers_normally(self): assert floor_by_factor(14.9, 5) == 10
    def test_floor_exact_numbers(self): assert floor_by_factor(5.0, 5) == 5
    def test_floor_negative_numbers(self): assert floor_by_factor(-1.1, 5) == -5
    def test_floor_zero(self): assert floor_by_factor(0, 10) == 0

    def test_ceil_numbers_normally(self): assert ceil_by_factor(11.1, 5) == 15
    def test_ceil_exact_numbers(self): assert ceil_by_factor(10.0, 5) == 10
    def test_ceil_negative_numbers(self): assert ceil_by_factor(-4.9, 5) == 0
    def test_ceil_zero(self): assert ceil_by_factor(0, 10) == 0

class TestImageResizer:
    """Tests to make sure screen images are resized correctly."""

    def test_normal_image_size(self):
        assert smart_resize_v15(1000, 1000) == (992, 992)
    
    def test_tiny_image_size(self):
        res = smart_resize_v15(100, 100)
        assert res == (256, 256)

    def test_huge_image_size(self):
        # Tests what happens if image is way too big
        res = smart_resize_v15(3000, 3000)
        assert res == (1344, 1344)

    def test_weird_image_shape(self):
        # Image is too tall and skinny
        assert smart_resize_v15(100, 2000) is None

    def test_zero_image_size(self):
        # Image has zero height
        assert smart_resize_v15(0, 500) is None

    def test_basic_box_math(self):
        assert normalize_box([50, 50], (100, 100)) == [0.5, 0.5, 0.5, 0.5]

    def test_advanced_box_math(self):
        assert normalize_box([10, 20, 30, 40], (100, 200)) == [0.1, 0.1, 0.3, 0.2]

    def test_box_math_with_resizing(self):
        assert normalize_box([10, 10], (100, 100), (200, 200)) == [0.05, 0.05, 0.05, 0.05]

    def test_decimal_box_math(self):
        assert normalize_box([10.5, 20.5], (100, 100)) == [0.105, 0.205, 0.105, 0.205]
    
    def test_backup_plan_works(self):
        # Tests the backup plan when resizing fails
        res = parse_actions("click(point=[0,0,0,0])", screen_context={"width": 0, "height": 100}, model_v15=True)
        assert res[0]["error"] is False

    def test_exact_image_size_limit(self):
        # Tests the exact image size limit so it doesn't get confused
        assert smart_resize_v15(100, 1000) is not None

    def test_zero_size_prints_warning(self, caplog):
        # Tests if the system prints the exact warning for zero sizes
        smart_resize_v15(0, 500)
        assert "XX" not in caplog.text
        assert "smart_resize_v15: zero dimension received" in caplog.text

    def test_weird_shape_prints_warning(self, caplog):
        # Tests if the system prints the exact warning for weird shapes
        smart_resize_v15(100, 2000)
        assert "XX" not in caplog.text
        assert "smart_resize_v15: aspect ratio too large" in caplog.text

    def test_backup_plan_prints_warning(self, caplog):
        # Tests if the system prints the warning when using the backup plan
        parse_actions("click(point=[0,0,0,0])", screen_context={"width": 0, "height": 100}, model_v15=True)
        assert "smart_resize_v15 returned None for screen_context" in caplog.text

class TestTextReader:
    """Tests to make sure the system understands text commands correctly."""

    def test_read_simple_click(self):
        res = parse_action_string("click(point=[500,500])")
        assert res["function"] == "click"
        assert res["args"]["start_box"] == "[500,500]"

    def test_read_click_with_different_names(self):
        res = parse_action_string("click(start_point=[0,0])")
        assert res["args"]["start_box"] == "[0,0]"

    def test_read_special_box_text(self):
        res = parse_action_string("click(point=<bbox>10 20</bbox>)")
        assert res["args"]["start_box"] == "(10,20)"

    def test_read_special_point_text(self):
        res = parse_action_string("click(point=<point>5 5</point>)")
        assert res["args"]["start_box"] == "(5,5)"

    def test_read_typing_command(self):
        res = parse_action_string("type(text='hello')")
        assert res["args"]["text"] == "hello"

    def test_read_dragging_command(self):
        res = parse_action_string("drag(start_point=[0,0], end_point=[1,1])")
        assert res["args"]["start_box"] == "[0,0]"
        assert res["args"]["end_box"] == "[1,1]"

    def test_read_waiting_command(self):
        assert parse_action_string("wait()")["function"] == "wait"

    def test_read_complex_sentence(self):
        res = parse_action_string("type(text='User: Hello!')")
        assert res["args"]["text"] == "User: Hello!"

    def test_read_junk_text(self):
        assert parse_action_string("not_an_action") is None

    def test_full_click_process(self):
        ctx = {"width": 1000, "height": 1000}
        res = parse_actions("click(point=[500,500,500,500])", screen_context=ctx)
        assert res[0]["action_type"] == "click"
        assert res[0]["action_inputs"]["start_coords"] == [500, 500]

    def test_full_drag_process(self):
        ctx = {"width": 1000, "height": 1000}
        res = parse_actions("drag(start_point=[0,0,0,0], end_point=[100,100,100,100])", screen_context=ctx)
        assert res[0]["action_inputs"]["end_coords"] == [100, 100]

    def test_full_typing_process(self):
        res = parse_actions("type(text='test')")
        assert res[0]["action_inputs"]["text"] == "test"

    def test_read_two_commands_together(self):
        actions = parse_actions("click(point=[1,1,1,1])\n\ntype(text='hi')")
        assert len(actions) == 2

    def test_read_command_with_action_prefix(self):
        res = parse_actions("Action: click(point=[0,0,0,0])")
        assert res[0]["action_type"] == "click"

    def test_read_command_with_extra_symbols(self):
        res = parse_action_string("<|box_start|>click(point=[0,0])<|box_end|>")
        assert res["function"] == "click"

    def test_read_input_command(self):
        res = parse_actions("input(value='xyz')")
        assert res[0]["action_inputs"]["text"] == "xyz"
        assert res[0]["action_type"] == "input"

    def test_ignore_bad_start_points(self):
        res = parse_actions("click(point=[10,20,30])", screen_context={"width": 100, "height": 100})
        assert "start_coords" not in res[0]["action_inputs"]

    def test_ignore_bad_end_points(self):
        res = parse_actions("drag(start_point=[0,0], end_point=[10,20,30])", screen_context={"width": 100, "height": 100})
        assert "end_coords" not in res[0]["action_inputs"]

    def test_read_box_with_parentheses(self):
        res = parse_actions("click(box=(100,100))")
        assert res[0]["action_inputs"]["box"] == [0.1, 0.1, 0.1, 0.1]

    def test_read_typing_with_content_word(self):
        res = parse_actions("type(content='hello')")
        assert res[0]["action_inputs"]["text"] == "hello"
        assert res[0]["action_type"] == "type"

    def test_ignore_resizing_if_no_screen(self):
        res = parse_actions("click(point=[0,0,0,0])", model_v15=True)
        assert res[0]["action_type"] == "click"

    def test_scaling_numbers_up(self):
        res = parse_actions("click(point=[0,0,0,0])", screen_context={"width": 100, "height": 100}, scale_factor=2.0)
        assert res[0]["action_inputs"]["start_coords"] == [0, 0]

    def test_cleaning_tricky_words(self):
        # Tests if tricky words break the text cleaner
        res = parse_action_string('type(text="helloX")')
        assert res["args"]["text"] == "helloX"

    def test_read_last_action_only(self):
        # Tests if the system correctly picks the last action when given multiple
        res = parse_actions("Action: click(point=[1,1,1,1])\n\nAction: type(text='hello')")
        assert len(res) == 1
        assert res[0]["action_type"] == "type"
