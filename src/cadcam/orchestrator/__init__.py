"""M4 多智能体编排层：状态机 / 黑板 / 角色 / 有界修复 / 情节记忆。"""
from cadcam.orchestrator.blackboard import Blackboard
from cadcam.orchestrator.memory import EpisodicMemory, failure_signature
from cadcam.orchestrator.runner import run_task
from cadcam.orchestrator.state import ALLOWED, IllegalTransition, StateMachine

__all__ = ["Blackboard", "EpisodicMemory", "failure_signature", "run_task",
           "StateMachine", "ALLOWED", "IllegalTransition"]
