# 游戏主控制器
import pygame
from snake import Snake
from food import Food
from display import Display
from constants import *
from utils import *


class Game:
    def __init__(self):
        """初始化游戏"""
        self.display = Display()
        self.snake = Snake()
        self.food = Food()
        self.score = 0
        self.game_over = False
        self.speed = INITIAL_SPEED

    def handle_input(self):
        """处理用户输入"""
        for event in self.display.get_events():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.KEYDOWN:
                if event.key == KEY_ESC:
                    return False
                elif event.key == KEY_R and self.game_over:
                    self.restart()
                elif not self.game_over:
                    self.handle_direction_input(event.key)
        return True

    def handle_direction_input(self, key):
        """处理方向输入"""
        if key in (KEY_UP, KEY_W):
            self.snake.change_direction(UP)
        elif key in (KEY_DOWN, KEY_S):
            self.snake.change_direction(DOWN)
        elif key in (KEY_LEFT, KEY_A):
            self.snake.change_direction(LEFT)
        elif key in (KEY_RIGHT, KEY_D):
            self.snake.change_direction(RIGHT)

    def update(self):
        """更新游戏状态"""
        if self.game_over:
            return

        # 移动蛇
        if not self.snake.move():
            self.game_over = True
            return

        # 检查是否吃到食物
        if self.snake.get_head() == self.food.get_position():
            self.snake.grow()
            self.food.respawn(self.snake.get_body())
            self.score += 10
            self.increase_speed()

    def increase_speed(self):
        """增加游戏速度"""
        if self.speed < MAX_SPEED:
            self.speed += SPEED_INCREMENT

    def draw(self):
        """绘制游戏"""
        self.display.clear_screen()
        self.display.draw_snake(self.snake.get_body())
        self.display.draw_food(self.food.get_position())
        self.display.draw_score(self.score)

        if self.game_over:
            self.display.draw_game_over(self.score)

        self.display.update_display()

    def restart(self):
        """重新开始游戏"""
        self.snake = Snake()
        self.food = Food()
        self.score = 0
        self.game_over = False
        self.speed = INITIAL_SPEED

    def run(self):
        """主游戏循环"""
        running = True
        while running:
            running = self.handle_input()
            self.update()
            self.draw()
            self.display.control_frame_rate(self.speed)

        self.display.quit()
