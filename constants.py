# 游戏常量配置
import pygame

# 窗口设置
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
GRID_SIZE = 20
GRID_WIDTH = WINDOW_WIDTH // GRID_SIZE
GRID_HEIGHT = WINDOW_HEIGHT // GRID_SIZE

# 颜色定义 (RGB)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)

# 游戏设置
INITIAL_SPEED = 10
SPEED_INCREMENT = 2
MAX_SPEED = 20

# 方向常量
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)

# 控制键
KEY_UP = pygame.K_UP
KEY_DOWN = pygame.K_DOWN
KEY_LEFT = pygame.K_LEFT
KEY_RIGHT = pygame.K_RIGHT
KEY_W = pygame.K_w
KEY_S = pygame.K_s
KEY_A = pygame.K_a
KEY_D = pygame.K_d
KEY_SPACE = pygame.K_SPACE
KEY_ESC = pygame.K_ESCAPE
KEY_R = pygame.K_r

# 字体设置
FONT_SIZE = 36
SMALL_FONT_SIZE = 24
