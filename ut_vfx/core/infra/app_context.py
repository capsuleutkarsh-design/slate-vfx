from typing import Optional

from .config_manager import ConfigManager
from .database_manager import DatabaseManager, database_manager
from .server_hub import ServerHub
from ..domain.central_attendance import CentralAttendance
from ..domain.asset_api import create_asset_api
from ..domain.library_manager import LibraryManager
from ..domain.user_manager import UserManager


class AppContext:
    """
    Lightweight dependency container for GUI composition.
    Keeps object creation in one place and enables test-time injection.
    """

    def __init__(
        self,
        user_manager: Optional[UserManager] = None,
        config_manager: Optional[ConfigManager] = None,
        db_manager: Optional[DatabaseManager] = None,
        server_hub: Optional[ServerHub] = None,
        attendance: Optional[CentralAttendance] = None,
        library_manager: Optional[LibraryManager] = None,
    ):
        self._user_manager = user_manager
        self._config_manager = config_manager
        self._db_manager = db_manager
        self._server_hub = server_hub
        self._attendance = attendance
        self._library_manager = library_manager
        self._asset_api = None

    def user_manager(self) -> UserManager:
        if self._user_manager is None:
            self._user_manager = UserManager()
        return self._user_manager

    def config_manager(self) -> ConfigManager:
        if self._config_manager is None:
            self._config_manager = ConfigManager()
        return self._config_manager

    def db_manager(self):
        if self._db_manager is None:
            self._db_manager = database_manager
        return self._db_manager

    def server_hub(self) -> ServerHub:
        if self._server_hub is None:
            self._server_hub = ServerHub()
        return self._server_hub

    def attendance(self) -> CentralAttendance:
        if self._attendance is None:
            self._attendance = CentralAttendance()
        return self._attendance

    def library_manager(self) -> LibraryManager:
        if self._library_manager is None:
            self._library_manager = LibraryManager(self.db_manager())
        return self._library_manager

    def asset_api(self):
        if self._asset_api is None:
            self._asset_api = create_asset_api(library_manager=self.library_manager())
        return self._asset_api
