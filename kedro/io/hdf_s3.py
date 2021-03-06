# Copyright 2018-2019 QuantumBlack Visual Analytics Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
# NONINFRINGEMENT. IN NO EVENT WILL THE LICENSOR OR OTHER CONTRIBUTORS
# BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF, OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The QuantumBlack Visual Analytics Limited ("QuantumBlack") name and logo
# (either separately or in combination, "QuantumBlack Trademarks") are
# trademarks of QuantumBlack. The License does not grant you any right or
# license to the QuantumBlack Trademarks. You may not use the QuantumBlack
# Trademarks or any confusingly similar mark as a trademark for your product,
#     or use the QuantumBlack Trademarks in any other manner that might cause
# confusion in the marketplace, including but not limited to in advertising,
# on websites, or on software.
#
# See the License for the specific language governing permissions and
# limitations under the License.

"""``HDFS3DataSet`` loads and saves data to an hdf file in S3. The
underlying functionality is supported by pandas HDFStore and PyTables,
so it supports all allowed PyTables options for loading and saving hdf files.
"""
import copy
from pathlib import PurePosixPath
from typing import Any, Dict

import pandas as pd
from s3fs import S3FileSystem

from kedro.io.core import AbstractVersionedDataSet, Version, deprecation_warning

HDFSTORE_DRIVER = "H5FD_CORE"


class HDFS3DataSet(AbstractVersionedDataSet):
    """``HDFS3DataSet`` loads and saves data to a S3 bucket. The
    underlying functionality is supported by pandas, so it supports all
    allowed pandas options for loading and saving hdf files.

    Example:
    ::

        >>> from kedro.io import HDFS3DataSet
        >>> import pandas as pd
        >>>
        >>> data = pd.DataFrame({'col1': [1, 2], 'col2': [4, 5],
        >>>                      'col3': [5, 6]})
        >>>
        >>> data_set = HDFS3DataSet(filepath="test.hdf",
        >>>                         bucket_name="test_bucket",
        >>>                         key="test_hdf_key",
        >>>                         load_args=None,
        >>>                         save_args=None)
        >>> data_set.save(data)
        >>> reloaded = data_set.load()
        >>>
        >>> assert data.equals(reloaded)

    """

    DEFAULT_LOAD_ARGS = {}  # type: Dict[str, Any]
    DEFAULT_SAVE_ARGS = {}  # type: Dict[str, Any]

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        filepath: str,
        key: str,
        bucket_name: str = None,
        credentials: Dict[str, Any] = None,
        load_args: Dict[str, Any] = None,
        save_args: Dict[str, Any] = None,
        version: Version = None,
        s3fs_args: Dict[str, Any] = None,
    ) -> None:
        """Creates a new instance of ``HDFS3DataSet`` pointing to a concrete
        hdf file on S3.

        Args:
            filepath: Path to an hdf file. May contain the full path in S3
                including bucket and protocol, e.g. `s3://bucket-name/path/to/file.hdf`.
            bucket_name: S3 bucket name. Must be specified **only** if not
                present in ``filepath``.
            key: Identifier to the group in the HDF store.
            credentials: Credentials to access the S3 bucket, such as
                ``aws_access_key_id``, ``aws_secret_access_key``.
            load_args: PyTables options for loading hdf files.
                You can find all available arguments at:
                https://www.pytables.org/usersguide/libref/top_level.html#tables.open_file
                All defaults are preserved.
            save_args: PyTables options for saving hdf files.
                You can find all available arguments at:
                https://www.pytables.org/usersguide/libref/top_level.html#tables.open_file
                All defaults are preserved.
            version: If specified, should be an instance of
                ``kedro.io.core.Version``. If its ``load`` attribute is
                None, the latest version will be loaded. If its ``save``
                attribute is None, save version will be autogenerated.
            s3fs_args: S3FileSystem options. You can find all available arguments at:
                https://s3fs.readthedocs.io/en/latest/api.html#s3fs.core.S3FileSystem

        """
        deprecation_warning(self.__class__.__name__)
        _credentials = copy.deepcopy(credentials) or {}
        _s3fs_args = copy.deepcopy(s3fs_args) or {}
        _s3 = S3FileSystem(client_kwargs=_credentials, **_s3fs_args)
        path = _s3._strip_protocol(filepath)  # pylint: disable=protected-access
        path = PurePosixPath("{}/{}".format(bucket_name, path) if bucket_name else path)

        super().__init__(
            path, version, exists_function=_s3.exists, glob_function=_s3.glob
        )
        self._key = key

        # Handle default load and save arguments
        self._load_args = copy.deepcopy(self.DEFAULT_LOAD_ARGS)
        if load_args is not None:
            self._load_args.update(load_args)
        self._save_args = copy.deepcopy(self.DEFAULT_SAVE_ARGS)
        if save_args is not None:
            self._save_args.update(save_args)

        self._s3 = _s3

    def _describe(self) -> Dict[str, Any]:
        return dict(
            filepath=self._filepath,
            key=self._key,
            load_args=self._load_args,
            save_args=self._save_args,
            version=self._version,
        )

    def _load(self) -> pd.DataFrame:
        load_path = self._get_load_path()

        with self._s3.open(str(load_path), mode="rb") as s3_file:
            binary_data = s3_file.read()

        with pd.HDFStore(
            str(self._filepath),
            mode="r",
            driver=HDFSTORE_DRIVER,
            driver_core_backing_store=0,
            driver_core_image=binary_data,
            **self._load_args,
        ) as store:
            return store[self._key]

    def _save(self, data: pd.DataFrame) -> None:
        save_path = str(self._get_save_path())

        with pd.HDFStore(
            str(self._filepath),
            mode="w",
            driver=HDFSTORE_DRIVER,
            driver_core_backing_store=0,
            **self._save_args,
        ) as store:
            store.put(self._key, data, format="table")
            # pylint: disable=protected-access
            binary_data = store._handle.get_file_image()

        with self._s3.open(save_path, mode="wb") as s3_file:
            # Only binary read and write modes are implemented for S3Files
            s3_file.write(binary_data)

    def _exists(self) -> bool:
        load_path = str(self._get_load_path())

        if self._s3.isfile(load_path):
            with self._s3.open(load_path, mode="rb") as s3_file:
                binary_data = s3_file.read()

            with pd.HDFStore(
                str(self._filepath),
                mode="r",
                driver=HDFSTORE_DRIVER,
                driver_core_backing_store=0,
                driver_core_image=binary_data,
                **self._load_args,
            ) as store:
                key_with_slash = (
                    self._key if self._key.startswith("/") else "/" + self._key
                )
                return key_with_slash in store.keys()
        return False
