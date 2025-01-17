#!/usr/bin/env python
"""exchangeable_loom

The exchangeable Loom format extends Loom spec 2.0.1 based on the proposed Loom
3.0 feature (https://github.com/linnarsson-lab/loompy/issues/51) and is defined
by the following minimum structure:

    /.attrs['LOOM_SPEC_VERSION'] = '3.0.0alpha'
    /global
    /global/manifest = table(columns=['loom_path', 'dtype', ...])
    /matrix
    /layers
    /col_attrs
    /col_graphs
    /row_attrs
    /row_graphs

For conversion between AnnData and exchangeable Loom, anndata.obsm, anndata.varm
and non-scalar value of anndata.uns are stored as datasets under /global, while
scalar value of anndata.uns are store at global attributes at /.attrs to make it
backward-compatible with Loom 2.0.1 and AnnData.

* Provides

    Read and write functions for conversion between AnnData and exchangeable
    Loom.
"""

import logging
import anndata
import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp


exchangeable_loom_version = '3.0.0alpha'


def _h5_read_coo_matrix(node):
    shape = tuple(map(int, node.attrs['shape'].decode().split(',')))
    return sp.coo_matrix((node['w'], (node['a'], node['b'])), shape=shape)


def _h5_write_coo_matrix(root, path, graph):
    if path not in root:
        root.create_group(path)
    graph_node = root[path]
    if not isinstance(graph_node, h5py.Group):
        raise ValueError(path)
    row_path = '{}/{}'.format(path, 'a')
    col_path = '{}/{}'.format(path, 'b')
    data_path = '{}/{}'.format(path, 'w')
    if row_path in root:
        del root[row_path]
    if col_path in root:
        del root[col_path]
    if data_path in root:
        del root[data_path]
    root.create_dataset(row_path, data=graph.row)
    root.create_dataset(col_path, data=graph.col)
    root.create_dataset(data_path, data=graph.data)
    graph_node.attrs['shape'] = (','.join(map(str, graph.shape))).encode()
    return 0


def _h5_read_csr_matrix(node):
    shape = tuple(map(int, node.attrs['shape'].decode().split(',')))
    return sp.coo_matrix((node['data'], (node['indices'], node['indptr'])), shape=shape)


def _h5_write_csr_matrix(root, path, graph):
    if path not in root:
        root.create_group(path)
    graph_node = root[path]
    if not isinstance(graph_node, h5py.Group):
        raise ValueError(path)
    data_path = '{}/{}'.format(path, 'data')
    indices_path = '{}/{}'.format(path, 'indices')
    indptr_path = '{}/{}'.format(path, 'indptr')
    if data_path in root:
        del root[data_path]
    if indices_path in root:
        del root[indices_path]
    if indptr_path in root:
        del root[indptr_path]
    root.create_dataset(data_path, data=graph.data)
    root.create_dataset(indices_path, data=graph.row)
    root.create_dataset(indptr_path, data=graph.col)
    graph_node.attrs['shape'] = (','.join(map(str, graph.shape))).encode()
    return 0


def _h5_write_recursive_dictionary(
        data,
        path,
        record,
        attr_root=None,
        dataset_root=None,
        graph_root=None,
):
    if isinstance(data, dict):
        for child in list(data.keys()):
            _h5_write_recursive_dictionary(
                data[child],
                '{}__{}'.format(path, child),
                record,
                attr_root=attr_root,
                dataset_root=dataset_root,
                graph_root=graph_root,
            )
    elif isinstance(data, (bytes, str, int, float, np.number, np.character)):
        dtype = type(data)
        if attr_root:
            if isinstance(data, str):
                data = data.encode()
            attr_root.attrs[path] = data
            record.append('{}.attrs[{}]::scalar'.format(attr_root.name, path))
        else:
            logging.warning('Ignoring attr {} ({})'.format(path, dtype))
    elif isinstance(data, (tuple, list, np.ndarray)):
        dtype = type(data)
        if dataset_root:
            if not isinstance(data, np.ndarray):
                data = np.array(data)
            if isinstance(data[0], str):
                data = data.astype(np.character)
            if path in dataset_root:
                del dataset_root[path]
            dataset_root.create_dataset(path, data=data)
            record.append('{}/{}::array'.format(dataset_root.name, path))
        else:
            logging.warning('Ignoring dataset {} ({})'.format(path, dtype))
    elif isinstance(data, sp.csr_matrix):
        dtype = type(data)
        if graph_root:
            _h5_write_coo_matrix(graph_root, path, sp.coo_matrix(data))
            record.append('{}/{}::graph'.format(graph_root.name, path))
        else:
            logging.warning('Ignoring graph {} ({})'.format(path, dtype))
    else:
        dtype = type(data)
        logging.warning('Ignoring {} ({})'.format(path, dtype))


def _is_exchangeable_loom(filename):
    with h5py.File(filename, mode='r') as lm:
        try:
            version = lm.attrs['LOOM_SPEC_VERSION'].decode()
        except Exception:
            version = None
        return version == exchangeable_loom_version


def _read_manifest(h5file):
    return pd.DataFrame(
        np.array(h5file['global']['manifest']).astype(str),
        columns=['loom_path', 'anndata_path', 'sce_path', 'dtype'],
    )


def read_exchangeable_loom(filename):
    """Read exchangeable Loom

    * Parameters
        + filename : str
        Path of the input exchangeable Loom file

    * Returns
        + adata : AnnData
        An AnnData object
    """
    # Use anndata to read matrix, obs and var
    adata = anndata.read_loom(filename)

    if not _is_exchangeable_loom(filename):
        return adata

    # Use h5py to read the rest
    with h5py.File(filename, mode='r') as lm:
        manifest = _read_manifest(lm)
        for i, row in manifest.iterrows():
            anndata_path = row['anndata_path']
            loom_path = row['loom_path']
            dtype = row['dtype']
            if anndata_path and loom_path:
                # Get data from loom_path
                if loom_path.startswith('/.attrs'):
                    lm_path = loom_path[8:-1]
                    data = lm.attrs[lm_path]
                else:
                    data = lm[loom_path]
                # Type conversion according to dtype
                if dtype == 'array':
                    data = np.array(data)
                    if isinstance(data.flat[0], bytes):
                        data = data.astype(str)
                elif dtype == 'graph':
                    data = sp.csr_matrix(_h5_read_coo_matrix(data))
                elif dtype == 'scalar':
                    if isinstance(data, bytes):
                        data = data.decode()

                # Put data to the right location according to anndata_path
                if anndata_path.startswith('/uns'):
                    # For .uns, write recursive dictionary as necessary
                    attr = adata.uns
                    paths = anndata_path[5:].split('/')
                    n_path = len(paths)
                    for i, path in enumerate(paths):
                        if i == n_path - 1:
                            attr[path] = data
                        else:
                            if path not in attr:
                                attr[path] = {}
                            attr = attr[path]
                elif anndata_path.startswith('/obsm'):
                    attr = anndata_path[6:]
                    adata.obsm[attr] = data
                elif anndata_path.startswith('/varm'):
                    attr = anndata_path[6:]
                    adata.varm[attr] = data
                else:
                    logging.warning('Unexpected anndata path: {}'.format(anndata_path))
    return adata


def write_exchangeable_loom(adata, filename):
    """Write an AnnData object to an exchangeable Loom

    * Parameters
        + adata : AnnData
        An AnnData object
        + filename : str
        Path of the output exchangeable Loom file
    """
    adata.write_loom(filename)
    n_obs, n_var = adata.X.shape
    manifest = {'loom': [], 'anndata': [], 'sce': [], 'dtype': []}
    with h5py.File(filename, mode='r+') as lm:
        # Write modified LOOM_SPEC_VERSION
        lm.attrs['LOOM_SPEC_VERSION'] = exchangeable_loom_version.encode()

        # Create necessary groups
        lm.create_group('/global')

        # Write /obsm
        for k in adata.obsm.keys():
            if k != "deconvolution_results":
                arr = adata.obsm[k]
                arr_name = k.replace('X_', '').upper()
                # Derive paths
                anndata_path = '/obsm/{}'.format(k)
                loom_path = '/global/reducedDims__{}'.format(arr_name)
                sce_path = '@reducedDims@listData${}'.format(arr_name)
                # Record mapping
                manifest['anndata'].append(anndata_path)
                manifest['loom'].append(loom_path)
                manifest['sce'].append(sce_path)
                manifest['dtype'].append('array')
                # Write content to loom
                lm.create_dataset(loom_path, data=arr)

        # Write /varm
        for k in adata.varm.keys():
            arr = adata.varm[k]
            # Derive paths
            anndata_path = '/varm/{}'.format(k)
            loom_path = '/global/varm__{}'.format(k)
            # Record mapping
            manifest['anndata'].append(anndata_path)
            manifest['loom'].append(loom_path)
            manifest['sce'].append('')
            manifest['dtype'].append('array')
            # Write content to loom
            lm.create_dataset(loom_path, data=arr)

        # Write /uns
        uns_entries = []
        for k in adata.uns.keys():
            item = adata.uns[k]
            # Special handling of 'neighbors' as it contains graphs
            if k == 'neighbors':
                # Write content to loom while recording loom path and dtype
                _h5_write_recursive_dictionary(
                    item,
                    k,
                    uns_entries,
                    attr_root=lm,
                    dataset_root=lm['/global'],
                    graph_root=lm['/col_graphs'],
                )
            else:
                # Write content to loom while recording loom path and dtype
                _h5_write_recursive_dictionary(
                    item,
                    k,
                    uns_entries,
                    attr_root=lm,
                    dataset_root=lm['/global'],
                )
            # Derive paths
            for entry in uns_entries:
                loom_path, sep, dtype = entry.partition('::')
                if loom_path.startswith('/col_graphs/'):
                    path = loom_path[12:]
                    sce_path = '@colGraphs${}'.format(path.replace('__', '.'))
                else:
                    if loom_path.startswith('/.attrs['):
                        path = loom_path[8:-1]
                    elif loom_path.startswith('/global/'):
                        path = loom_path[8:]
                    else:
                        logging.warning('unexpected path: {}'.format(loom_path))
                    sce_path = '@metadata${}'.format(path.replace('__', '.'))
                anndata_path = '/uns/{}'.format(path.replace('__', '/'))
                # Record mapping
                manifest['anndata'].append(anndata_path)
                manifest['loom'].append(loom_path)
                manifest['sce'].append(sce_path)
                manifest['dtype'].append(dtype)

        # Write mapping
        lm['/global'].create_dataset(
            'manifest', data=pd.DataFrame(manifest).values.astype(np.character))