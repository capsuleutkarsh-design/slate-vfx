from dataclasses import dataclass
from datetime import datetime

@dataclass
class Project:
    code: str
    name: str
    excel_path: str
    sheet_url: str
    base_path: str
    last_accessed: datetime
    status: str

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            code=data.get('code', ''),
            name=data.get('name', ''),
            excel_path=data.get('excel_path', ''),
            sheet_url=data.get('sheet_url', ''),
            base_path=data.get('base_path', ''),
            last_accessed=datetime.fromisoformat(data.get('last_accessed', datetime.now().isoformat())),
            status=data.get('status', 'active')
        )

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'name': self.name,
            'excel_path': self.excel_path,
            'sheet_url': self.sheet_url,
            'base_path': self.base_path,
            'last_accessed': self.last_accessed.isoformat(),
            'status': self.status
        }
