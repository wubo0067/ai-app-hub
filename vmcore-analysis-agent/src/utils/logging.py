import logging
import os


# Define a custom path filter to shorten log pathnames
def custom_path_filter(path):
    # Define the project root name
    project_root = "vmcore-analysis-agent"

    # Find the index of the project root in the path
    idx = path.find(project_root)
    if idx != -1:
        # Extract the portion of the path after the project root
        path = path[
            idx + len(project_root) + 1 :
        ]  # +1 to include the separator after project root
    else:
        # If project root is not found, return the basename of the file
        path = os.path.basename(path)
    return path


# Define a custom LogRecord class to modify the pathname
class CustomLogRecord(logging.LogRecord):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pathname = custom_path_filter(self.pathname)


# Function to set up the logger
def setup_logger(log_filename="va-agent.log", log_dir="logs"):
    # Ensure the logging directory exists
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Define the log file path
    log_filepath = os.path.join(log_dir, log_filename)

    # Get or create logger
    logger_instance = logging.getLogger("vmcore_analysis_agent")

    # Avoid adding handlers multiple times if logger already exists
    if not logger_instance.handlers:
        # Define the logging configuration
        logger_instance = logging.getLogger("vmcore_analysis_agent")
        logging.setLogRecordFactory(CustomLogRecord)  # Only set once globally
        handler = logging.FileHandler(log_filepath)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(module)s] [%(pathname)s]: %(message)s"
        )
        handler.setFormatter(formatter)
        logger_instance.addHandler(handler)
        logger_instance.setLevel(logging.INFO)

        # Also add console handler for debugging
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger_instance.addHandler(console_handler)

    return logger_instance


# Global logger object
logger = setup_logger()
