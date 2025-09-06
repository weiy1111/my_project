# 食物类
from constants import *
from utils import *


class Food:
    def __init__(self, snake_body=None):
        """初始化食物"""
        self.position = self.generate_position(snake_body or [])

    def generate_position(self, snake_body):
        """生成食物位置，避免与蛇身重叠"""
        while True:
            position = random_position()
            if position not in snake_body:
                return position

    def respawn(self, snake_body):
        """重新生成食物"""
        self.position = self.generate_position(snake_body)

    def get_position(self):
        """获取食物位置"""
        return self.position
