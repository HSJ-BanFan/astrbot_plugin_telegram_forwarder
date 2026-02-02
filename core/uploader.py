import os
import math
import asyncio
import httpx
from urllib.parse import urlparse, quote
from typing import Optional
from astrbot.api import logger


class FileUploader:
    def __init__(self, proxy_url: str = None):
        self.proxy_url = proxy_url

    async def upload(self, fpath: str, hosting_url: str) -> Optional[str]:
        """
        Upload a file to the hosting service, choosing strategy based on file size.
        """
        if not hosting_url:
            return None

        file_size = os.path.getsize(fpath)
        # Use simple upload for < 20MB, chunked for larger
        if file_size > 20 * 1024 * 1024:
            logger.info(
                f"File > 20MB ({file_size} bytes), using Chunked Upload for {os.path.basename(fpath)}..."
            )
            async with httpx.AsyncClient(proxy=self.proxy_url, timeout=300.0) as client:
                return await self._upload_chunked(client, hosting_url, fpath)
        else:
            return await self._upload_simple(hosting_url, fpath)

    async def _upload_simple(self, hosting_url: str, fpath: str) -> Optional[str]:
        """
        Standard multipart/form-data upload.
        """
        async with httpx.AsyncClient(proxy=self.proxy_url, timeout=120.0) as client:
            filename = os.path.basename(fpath)
            # Add directory parameter
            params = {"uploadFolder": "Telegram/Media"}

            # Retry logic
            for attempt in range(3):
                try:
                    with open(fpath, "rb") as f:
                        files = {"file": (filename, f, "application/octet-stream")}
                        resp = await client.post(
                            hosting_url, files=files, params=params
                        )

                        if resp.status_code == 200:
                            return self._extract_upload_url(resp.json(), hosting_url)
                        else:
                            logger.error(
                                f"Upload failed (Attempt {attempt + 1}): {resp.status_code} {resp.text}"
                            )
                            if attempt == 2:
                                break
                            await asyncio.sleep(2)
                except (
                    httpx.ConnectError,
                    httpx.RemoteProtocolError,
                    httpx.WriteError,
                    httpx.ReadTimeout,
                ) as e:
                    logger.warning(f"Upload network error (Attempt {attempt + 1}): {e}")
                    if attempt == 2:
                        break
                    await asyncio.sleep(2)
        return None

    async def _upload_chunked(
        self, uploader: httpx.AsyncClient, hosting_url: str, fpath: str
    ) -> Optional[str]:
        """
        Chunked upload implementation.
        Init -> Chunks -> Merge -> Poll
        """
        try:
            chunk_size = 20 * 1024 * 1024  # 20MB
            file_size = os.path.getsize(fpath)
            total_chunks = math.ceil(file_size / chunk_size)
            original_filename = os.path.basename(fpath)

            ext = os.path.splitext(fpath)[1].lower()
            original_filetype = "application/octet-stream"
            if ext == ".flac":
                original_filetype = "audio/flac"
            elif ext == ".mp3":
                original_filetype = "audio/mpeg"

            # 1. Init
            params = {"uploadFolder": "Telegram/Media", "initChunked": "true"}
            data = {
                "totalChunks": str(total_chunks),
                "originalFileName": original_filename,
                "originalFileType": original_filetype,
            }
            dummy_files = {"_force_multipart": ("", b"")}

            resp = await uploader.post(
                hosting_url, params=params, data=data, files=dummy_files, timeout=30
            )
            if resp.status_code != 200:
                logger.error(f"Chunked Init failed: {resp.status_code} {resp.text}")
                return None

            resp_data = resp.json()
            upload_id = None
            if isinstance(resp_data, dict):
                upload_id = resp_data.get("data") or resp_data.get("uploadId")
            if not upload_id:
                return None

            # 2. Upload Chunks
            with open(fpath, "rb") as f:
                for i in range(total_chunks):
                    chunk_data = f.read(chunk_size)
                    q_params = {"uploadFolder": "Telegram/Media", "chunked": "true"}
                    b_data = {
                        "uploadId": upload_id,
                        "chunkIndex": str(i),
                        "totalChunks": str(total_chunks),
                        "originalFileName": original_filename,
                        "originalFileType": original_filetype,
                    }
                    files = {"file": (original_filename, chunk_data, original_filetype)}

                    for attempt in range(3):
                        try:
                            c_resp = await uploader.post(
                                hosting_url,
                                params=q_params,
                                data=b_data,
                                files=files,
                                timeout=300,
                            )
                            if c_resp.status_code != 200:
                                await asyncio.sleep(2)
                                continue
                            break
                        except Exception:
                            await asyncio.sleep(2)
                    else:
                        return None  # Failed after retries

            # 3. Merge
            m_params = {
                "uploadFolder": "Telegram/Media",
                "chunked": "true",
                "merge": "true",
            }
            m_data = {
                "uploadId": upload_id,
                "totalChunks": str(total_chunks),
                "originalFileName": original_filename,
                "originalFileType": original_filetype,
            }
            # Note: files=... is needed to force multipart format if data doesn't trigger it,
            # though usually data with file-like objects does. Here we used dummy in original code.
            # We'll use the same dummy trick to be safe.
            m_resp = await uploader.post(
                hosting_url,
                params=m_params,
                data=m_data,
                files={"_force_multipart": ("", b"")},
                timeout=60,
            )

            final_resp = None
            if m_resp.status_code == 200:
                final_resp = m_resp.json()
            elif m_resp.status_code == 202:
                # Async polling
                check_url = m_resp.json().get("statusCheckUrl")
                if not check_url:
                    check_url = f"/upload?uploadId={upload_id}&statusCheck=true&chunked=true&merge=true"

                if check_url.startswith("/"):
                    parsed = urlparse(hosting_url)
                    root = f"{parsed.scheme}://{parsed.netloc}"
                    check_url = root + check_url

                if "authCode=" not in check_url and "authCode=" in hosting_url:
                    try:
                        auth_code = hosting_url.split("authCode=")[1].split("&")[0]
                        check_url += (
                            f"&authCode={auth_code}"
                            if "?" in check_url
                            else f"?authCode={auth_code}"
                        )
                    except:
                        pass

                for _ in range(20):
                    await asyncio.sleep(2)
                    try:
                        poll_resp = await uploader.get(check_url, timeout=30)
                        if poll_resp.status_code == 200:
                            json_data = poll_resp.json()
                            if json_data.get("status") == "success" or json_data.get(
                                "url"
                            ):
                                final_resp = json_data
                                break
                        elif poll_resp.status_code != 202:
                            break
                    except Exception:
                        pass

            if final_resp:
                return self._extract_upload_url(final_resp, hosting_url)
            return None

        except Exception as e:
            logger.error(f"Chunked Upload Exception: {e}")
            return None

    def _extract_upload_url(self, res_json: dict, base_url: str) -> str:
        """Extract URL from response"""
        parsed = urlparse(base_url)
        root_url = f"{parsed.scheme}://{parsed.netloc}"

        extractors = [
            lambda r: root_url + r[0]["src"]
            if isinstance(r, list) and r and "src" in r[0]
            else None,
            lambda r: r.get("url"),
            lambda r: r.get("data", {}).get("url")
            if isinstance(r.get("data"), dict)
            else None,
            lambda r: root_url + r["result"][0]["src"]
            if isinstance(r, dict)
            and "result" in r
            and isinstance(r["result"], list)
            and r["result"]
            and "src" in r["result"][0]
            else None,
        ]

        for extractor in extractors:
            try:
                link = extractor(res_json)
                if link:
                    if link.startswith("/"):
                        link = root_url + link
                    return quote(link, safe=":/")
            except:
                continue

        return "[Unknown Link]"
