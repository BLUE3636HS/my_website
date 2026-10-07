"""Local uploads are supported only on a persistent, writable local host."""
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles


def on_vercel():
    return os.environ.get("VERCEL") == "1"


def prepare_upload_directory(directory):
    if not on_vercel():
        Path(directory).mkdir(parents=True, exist_ok=True)


def require_persistent_file_storage():
    if on_vercel():
        raise HTTPException(503, "ファイルの永続保存先が未設定のため、画像・PDFの保存は現在利用できません。")


class UploadStaticFiles(StaticFiles):
    """An absent uploads folder is a normal empty store, not a startup error."""
    def __init__(self, directory):
        super().__init__(directory=str(directory), check_dir=False)

    async def check_config(self):
        if Path(self.directory).exists():
            await super().check_config()
