# 显示界面类
import pygame
from constants import *
from utils import *


class Display:
    def __init__(self):
        """初始化显示"""
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Snake Game")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, FONT_SIZE)
        self.small_font = pygame.font.Font(None, SMALL_FONT_SIZE)

    def draw_snake(self, snake_body):
        """绘制蛇"""
        for i, segment in enumerate(snake_body):
            x, y = segment
            rect = pygame.Rect(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)

            # 蛇头用不同颜色
            if i == 0:
                pygame.draw.rect(self.screen, GREEN, rect)
            else:
                pygame.draw.rect(self.screen, BLUE, rect)

            # 绘制边框
            pygame.draw.rect(self.screen, BLACK, rect, 1)

    def draw_food(self, food_position):
        """绘制食物"""
        x, y = food_position
        rect = pygame.Rect(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
        pygame.draw.rect(self.screen, RED, rect)
        pygame.draw.rect(self.screen, BLACK, rect, 1)

    def draw_score(self, score):
        """绘制分数"""
        score_text = self.small_font.render(format_score(score), True, WHITE)
        self.screen.blit(score_text, (10, 10))

    def draw_game_over(self, score):
        """绘制游戏结束界面"""
        # 半透明背景
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))
        overlay.set_alpha(128)
        overlay.fill(BLACK)
        self.screen.blit(overlay, (0, 0))

        # 游戏结束文字
        game_over_text = self.font.render(format_game_over(score), True, WHITE)
        restart_text = self.small_font.render(format_restart_hint(), True, WHITE)

        # 居中显示
        game_over_rect = game_over_text.get_rect(center=(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 50))
        restart_rect = restart_text.get_rect(center=(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 50))

        self.screen.blit(game_over_text, game_over_rect)
        self.screen.blit(restart_text, restart_rect)

    def clear_screen(self):
        """清空屏幕"""
        self.screen.fill(BLACK)

    def update_display(self):
        """更新显示"""
        pygame.display.flip()

    def control_frame_rate(self, fps):
        """控制帧率"""
        self.clock.tick(fps)

    def get_events(self):
        """获取事件"""
        return pygame.event.get()

    def quit(self):
        """退出pygame"""
        pygame.quit()
