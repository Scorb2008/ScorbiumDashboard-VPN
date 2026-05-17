import sys
from pathlib import Path
from loguru import logger

class AppLogger:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, 'initialized'):
            return
        
        try:
            from app.core.config import config
            if config is None:
                raise AttributeError("Configuration is not loaded")
            
            log_dir = config.utils.log_path 
            rotation = config.utils.log_rotation
            retention = config.utils.log_retention
            log_level = config.utils.log_level
            
        except (ImportError, AttributeError):
            log_dir = "logs"
            rotation = "1 day"
            retention = "30 days"
            log_level = "INFO"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        logger.remove()
        
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
            level=log_level,
            colorize=True
        )
        
        logger.add(
            self.log_dir / "app_{time:YYYY-MM-DD}.log",
            rotation=rotation,
            retention=retention,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            compression="zip",
            enqueue=True 
        )
        
        logger.add(
            self.log_dir / "errors_{time:YYYY-MM-DD}.log",
            rotation=rotation,
            retention=retention,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="ERROR",
            compression="zip",
            enqueue=True
        )
        
        self.initialized = True
        self.logger = logger
    
    def get_logger(self):
        return self.logger

app_logger = AppLogger().get_logger()
log = app_logger

def initialize_config(config_instance):
    try:
        config_instance.initialize(logger=log)
    except Exception as e:
        log.error(f"Error initializing config: {e}")
