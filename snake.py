# 蛇类
from constants import *
from utils import *


class Snake:
    def __init__(self):
        """初始化蛇"""
        self.body = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]  # 蛇身列表，头在前
        self.direction = RIGHT  # 初始方向
        self.grow_next = False  # 是否需要生长

    def move(self):
        """移动蛇"""
        head = self.body[0]
        new_head = add_positions(head, self.direction)

        # 检查是否撞墙
        if not is_valid_position(new_head):
            return False  # 游戏结束

        # 检查是否撞到自己
        if new_head in self.body:
            return False  # 游戏结束

        # 添加新头部
        self.body.insert(0, new_head)

        # 如果不需要生长，移除尾部
        if not self.grow_next:
            self.body.pop()
        else:
            self.grow_next = False

        return True  # 移动成功

    def grow(self):
        """标记蛇需要生长"""
        self.grow_next = True

    def change_direction(self, new_direction):
        """改变方向"""
        # 防止反向移动
        if new_direction != get_opposite_direction(self.direction):
            self.direction = new_direction

    def get_head(self):
        """获取蛇头位置"""
        return self.body[0]

    def get_body(self):
        """获取蛇身所有位置"""
        return self.body.copy()

    def get_length(self):
        """获取蛇的长度"""
        return len(self.body)
