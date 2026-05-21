# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Download file.
"""

import shutil
import tarfile
import zipfile
import six
import requests
import os
import sys
from tqdm import tqdm

# Maximum time (seconds) we will wait for the server to *start* sending data.
# We keep no overall read timeout because model archives can take a long time
# to stream over slow links; the per-chunk iterator handles cancellation.
DEFAULT_CONNECT_TIMEOUT = 30


def http_get(url, path, extract: bool = True, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT):
    """
    Downloads a URL to a given path on disc.

    The HTTP connection is bounded by ``connect_timeout`` to avoid hanging on
    unresponsive servers. ``extract`` triggers safe archive extraction that
    rejects members whose resolved path would escape the destination directory.
    """
    if os.path.dirname(path) != '':
        os.makedirs(os.path.dirname(path), exist_ok=True)

    req = requests.get(url, stream=True, timeout=(connect_timeout, None))
    if req.status_code != 200:
        print("Exception when trying to download {}. Response {}".format(url, req.status_code), file=sys.stderr)
        req.raise_for_status()
        return

    download_filepath = path + "_part"
    with open(download_filepath, "wb") as file_binary:
        content_length = req.headers.get('Content-Length')
        total = int(content_length) if content_length is not None else None
        progress = tqdm(unit="B", total=total, unit_scale=True)
        for chunk in req.iter_content(chunk_size=1024):
            if chunk:  # filter out keep-alive new chunks
                progress.update(len(chunk))
                file_binary.write(chunk)

    os.replace(download_filepath, path)
    progress.close()

    if extract:
        data_dir = os.path.dirname(os.path.abspath(path))
        _extract_archive(path, data_dir, 'auto')


def _is_within_directory(directory: str, target: str) -> bool:
    """Return True iff *target* resolves to a path inside *directory*."""
    directory_abs = os.path.realpath(directory)
    target_abs = os.path.realpath(target)
    return os.path.commonpath([directory_abs, target_abs]) == directory_abs


def _safe_tar_members(archive: tarfile.TarFile, dest_dir: str):
    """Yield only tar members that are safe to extract into ``dest_dir``.

    Rejects absolute paths, parent-relative paths, and symlinks/hardlinks whose
    target leaves the destination directory (CVE-2007-4559 / Bandit B202).
    """
    for member in archive.getmembers():
        member_path = os.path.join(dest_dir, member.name)
        if not _is_within_directory(dest_dir, member_path):
            raise ValueError(
                f"Refusing to extract unsafe tar member outside destination: {member.name!r}"
            )
        if member.issym() or member.islnk():
            link_target = os.path.join(os.path.dirname(member_path), member.linkname)
            if not _is_within_directory(dest_dir, link_target):
                raise ValueError(
                    f"Refusing to extract unsafe tar link: {member.name!r} -> {member.linkname!r}"
                )
        if member.isdev():
            raise ValueError(f"Refusing to extract device file from tar: {member.name!r}")
        yield member


def _safe_zip_names(archive: zipfile.ZipFile, dest_dir: str):
    """Validate every zip entry resolves inside ``dest_dir`` before extraction."""
    for info in archive.infolist():
        if info.filename.startswith('/') or '..' in info.filename.replace('\\', '/').split('/'):
            raise ValueError(
                f"Refusing to extract unsafe zip entry: {info.filename!r}"
            )
        target = os.path.join(dest_dir, info.filename)
        if not _is_within_directory(dest_dir, target):
            raise ValueError(
                f"Refusing to extract unsafe zip entry outside destination: {info.filename!r}"
            )


def _extract_archive(file_path, path='.', archive_format='auto'):
    """
    Extracts an archive if it matches tar, tar.gz, tar.bz, or zip formats.

    :param file_path: path to the archive file
    :param path: path to extract the archive file
    :param archive_format: Archive format to try for extracting the file.
        Options are 'auto', 'tar', 'zip', and None.
        'tar' includes tar, tar.gz, and tar.bz files.
        The default 'auto' is ['tar', 'zip'].
        None or an empty list will return no matches found.

    :return: True if a match was found and an archive extraction was completed,
        False otherwise.
    """
    if archive_format is None:
        return False
    if archive_format == 'auto':
        archive_format = ['tar', 'zip']
    if isinstance(archive_format, six.string_types):
        archive_format = [archive_format]

    os.makedirs(path, exist_ok=True)

    for archive_type in archive_format:
        if archive_type == 'tar':
            open_fn = tarfile.open
            is_match_fn = tarfile.is_tarfile
        elif archive_type == 'zip':
            open_fn = zipfile.ZipFile
            is_match_fn = zipfile.is_zipfile
        else:
            continue

        if is_match_fn(file_path):
            with open_fn(file_path) as archive:
                try:
                    if archive_type == 'tar':
                        members = list(_safe_tar_members(archive, path))
                        archive.extractall(path, members=members)  # nosec B202
                    else:
                        _safe_zip_names(archive, path)
                        archive.extractall(path)  # nosec B202
                except (tarfile.TarError, zipfile.BadZipFile, ValueError, RuntimeError,
                        KeyboardInterrupt):
                    if os.path.exists(path):
                        if os.path.isfile(path):
                            os.remove(path)
                        else:
                            shutil.rmtree(path)
                    raise
            return True
    return False
