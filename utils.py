# 辅助工具函数
import random
from constants import *


def random_position():
    """生成随机位置"""
    x = random.randint(0, GRID_WIDTH - 1)
    y = random.randint(0, GRID_HEIGHT - 1)
    return (x, y)


def add_positions(pos1, pos2):
    """位置相加"""
    return (pos1[0] + pos2[0], pos1[1] + pos2[1])


def is_valid_position(position):
    """检查位置是否有效"""
    x, y = position
    return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT


def get_opposite_direction(direction):
    """获取相反方向"""
    opposites = {
        UP: DOWN,
        DOWN: UP,
        LEFT: RIGHT,
        RIGHT: LEFT
    }
    return opposites.get(direction, (0, 0))


def format_score(score):
    """格式化分数显示"""
    return f"Score: {score}"


def format_game_over(score):
    """格式化游戏结束信息"""
    return f"Game Over! Final Score: {score}"


def format_restart_hint():
    """格式化重新开始提示"""
    return "Press R to restart or ESC to quit"
