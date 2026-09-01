from aiogram.fsm.state import State, StatesGroup

class CatchMessageState(StatesGroup):
    message = State()


class AdminState(StatesGroup):
    """Prompts opened from the /admin panel."""
    set_balance = State()
    ban = State()
    refund = State()
