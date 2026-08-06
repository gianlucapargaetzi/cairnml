"""
Example of uploading files to remote storage and streaming them back to memory

Uploads two small sample files to the given storage location, then reads their contents
back with StorageManager.get_stream() - the content is streamed directly to memory as
bytes chunks, without being written to disk or stored in the local cache.

Works with any storage provider supported by the SDK, for example:

    python upload_and_stream.py s3://my-bucket/examples
    python upload_and_stream.py gs://my-bucket/examples
    python upload_and_stream.py azure://account.blob.core.windows.net/container/examples
    python upload_and_stream.py https://files.myclearml.com/examples

Credentials are taken from your clearml.conf (sdk.aws.s3 / sdk.google.storage / sdk.azure.storage).
"""
import argparse
import os
from io import BytesIO
from tempfile import gettempdir

from clearml.storage import StorageManager


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="Remote storage location to upload to, e.g. s3://my-bucket/examples")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    # create two local sample files
    local_files = []
    for i in range(2):
        path = os.path.join(gettempdir(), "sample_%d.txt" % i)
        with open(path, "w") as f:
            f.write("Hello from sample file %d!\n" % i * 10)
        local_files.append(path)

    # upload them
    remote_urls = []
    for path in local_files:
        remote_url = StorageManager.upload_file(path, "%s/%s" % (base_url, os.path.basename(path)))
        print("uploaded %s -> %s" % (path, remote_url))
        remote_urls.append(remote_url)

    # stream them back, entirely in memory - no disk writes, no cache
    for remote_url in remote_urls:
        stream = StorageManager.get_stream(remote_url)
        if stream is None:
            print("failed streaming %s" % remote_url)
            continue
        buffer = BytesIO()
        for chunk in stream:
            buffer.write(chunk)
        content = buffer.getvalue()
        print("streamed %s (%d bytes): %r..." % (remote_url, len(content), content[:40]))


if __name__ == "__main__":
    main()
