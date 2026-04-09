import json
from pathlib import Path


class MemoryStore:
    """
    Handles persistent storage for agent conversation history using a JSON file.
    
    It supports appending new messages and loading history with a maximum 
    message limit (truncation).
    """

    def __init__(self, path: str | Path, max_history: int = 50):
        """
        Initialize the memory store.
        
        Args:
            path: Path to the JSON file where memory is stored.
            max_history: Maximum number of messages to retrieve from history.
        """
        self.path = Path(path).expanduser().resolve()
        self.max_history = max_history
        # Ensure the directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> list[dict]:
        """
        Load conversation history from the JSON file.
        
        Returns:
            A list of message dictionaries, limited to the last `max_history` messages.
            Returns an empty list if the file does not exist.
        """
        if not self.path.exists():
            return []
        
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Ensure it is a list
            if not isinstance(data, list):
                return []
            
            # Return only the last `max_history` items
            return data[-self.max_history:]
        except (json.JSONDecodeError, OSError):
            # If the file is corrupted or unreadable, return empty history
            return []

    def append(self, messages: list[dict]) -> None:
        """
        Append new messages to the persistent storage.
        
        Args:
            messages: A list of message dictionaries to be added to the history.
        """
        existing = []
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, OSError):
                existing = []

        # Add the new messages
        existing.extend(messages)
        
        # Write back to disk
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except OSError:
            # Silent fail for now, but in production we might want to log this
            pass
