"""PyAutoGUI desktop actions."""
import time


def _gui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        return pyautogui
    except ImportError:
        raise ImportError("Visual agent requires pyautogui: pip install noodle[visual]")


def click(x: int, y: int):
    _gui().click(x, y)


def right_click(x: int, y: int):
    _gui().rightClick(x, y)


def double_click(x: int, y: int):
    _gui().doubleClick(x, y)


def type_text(text: str):
    _gui().typewrite(text, interval=0.05)


def press_key(key: str):
    _gui().press(key)


def drag(src_x: int, src_y: int, dst_x: int, dst_y: int):
    gui = _gui()
    gui.moveTo(src_x, src_y)
    gui.dragTo(dst_x, dst_y, duration=0.5)


def scroll(direction: str, clicks: int = 3):
    amount = clicks if direction == "up" else -clicks
    _gui().scroll(amount)


def scroll_to_image(template_path: str, max_attempts: int = 10):
    """Scroll down until template found or attempts exhausted."""
    from .matcher import find_on_screen
    for _ in range(max_attempts):
        coords = find_on_screen(template_path)
        if coords:
            return coords
        scroll("down", clicks=3)
        time.sleep(0.3)
    return None
