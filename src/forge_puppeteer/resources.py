from enum import StrEnum
from .actions import ExpressionAction,GestureAction,GripAction,LookAtAction,MoveAction,PerformanceAction,SpeakAction

class ActionResource(StrEnum):
    MOTION='motion'; GAZE='gaze'; EXPRESSION='expression'; VOICE='voice'; GRIPPER='gripper'

def default_action_resources(action:PerformanceAction)->frozenset[ActionResource]:
    if isinstance(action,(GestureAction,MoveAction)): return frozenset({ActionResource.MOTION})
    if isinstance(action,ExpressionAction): return frozenset({ActionResource.EXPRESSION})
    if isinstance(action,LookAtAction): return frozenset({ActionResource.GAZE})
    if isinstance(action,SpeakAction): return frozenset({ActionResource.VOICE})
    if isinstance(action,GripAction): return frozenset({ActionResource.GRIPPER})
    return frozenset()
