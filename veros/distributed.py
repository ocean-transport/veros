from . import runtime_settings as rs, runtime_state as rst
from .decorators import dist_context_only

SCATTERED_DIMENSIONS = (
    ('xt', 'xu'),
    ('yt', 'yu')
)


def send(buf, dest, comm, tag=None):
    kwargs = {}
    if tag is not None:
        kwargs.update(tag=tag)

    if rs.backend == 'jax':
        from mpi4jax import Send
        return Send(buf, dest=dest, comm=comm, **kwargs)

    return comm.Send(ascontiguousarray(buf), dest=dest, **kwargs)


def recv(buf, source, comm, tag=None):
    kwargs = {}
    if tag is not None:
        kwargs.update(tag=tag)

    if rs.backend == 'jax':
        from mpi4jax import Recv
        return Recv(buf, source=source, comm=comm, **kwargs)

    comm.Recv(buf, source=source, **kwargs)
    return buf


def sendrecv(sendbuf, recvbuf, source, dest, comm, sendtag=None, recvtag=None):
    kwargs = {}

    if send is not None:
        kwargs.update(sendtag=sendtag)

    if recvtag is not None:
        kwargs.update(recvtag=recvtag)

    if rs.backend == 'jax':
        from mpi4jax import Sendrecv
        return Sendrecv(sendbuf, recvbuf, source=source, dest=dest, comm=comm, **kwargs)

    comm.Sendrecv(
        sendbuf=ascontiguousarray(sendbuf), recvbuf=recvbuf,
        source=source, dest=dest,
        **kwargs
    )
    return recvbuf


def allreduce(buf, op, comm):
    if rs.backend == 'jax':
        from mpi4jax import Allreduce
        return Allreduce(buf, op=op, comm=comm)

    from veros.core.operators import numpy as np
    recvbuf = np.empty_like(buf)
    comm.Allreduce(ascontiguousarray(buf), recvbuf, op=op)
    return recvbuf


def ascontiguousarray(arr):
    assert rs.backend == 'numpy'
    import numpy
    return numpy.ascontiguousarray(arr)


def validate_decomposition(nx, ny):
    if rs.mpi_comm is None:
        if (rs.num_proc[0] > 1 or rs.num_proc[1] > 1):
            raise RuntimeError('mpi4py is required for distributed execution')
        return

    comm_size = rs.mpi_comm.Get_size()
    proc_num = rs.num_proc[0] * rs.num_proc[1]
    if proc_num != comm_size:
        raise RuntimeError('number of processes ({}) does not match size of communicator ({})'
                           .format(proc_num, comm_size))

    if nx % rs.num_proc[0]:
        raise ValueError('processes do not divide domain evenly in x-direction')

    if ny % rs.num_proc[1]:
        raise ValueError('processes do not divide domain evenly in y-direction')


def get_chunk_size(nx, ny):
    return (nx // rs.num_proc[0], ny // rs.num_proc[1])


def get_global_size(nx, ny, arr_shp, dim_grid, include_overlap=False):
    ovl = 4 if include_overlap else 0
    shape = []
    for s, dim in zip(arr_shp, dim_grid):
        if dim in SCATTERED_DIMENSIONS[0]:
            shape.append(nx + ovl)
        elif dim in SCATTERED_DIMENSIONS[1]:
            shape.append(ny + ovl)
        else:
            shape.append(s)
    return shape


def get_local_size(nx, ny, arr_shp, dim_grid, include_overlap=False):
    ovl = 4 if include_overlap else 0
    shape = []
    for s, dim in zip(arr_shp, dim_grid):
        if dim in SCATTERED_DIMENSIONS[0]:
            shape.append(nx // rs.num_proc[0] + ovl)
        elif dim in SCATTERED_DIMENSIONS[1]:
            shape.append(ny // rs.num_proc[1] + ovl)
        else:
            shape.append(s)
    return shape


def proc_rank_to_index(rank):
    return (rank % rs.num_proc[0], rank // rs.num_proc[0])


def proc_index_to_rank(ix, iy):
    return ix + iy * rs.num_proc[0]


def get_chunk_slices(nx, ny, dim_grid, proc_idx=None, include_overlap=False):
    if proc_idx is None:
        proc_idx = proc_rank_to_index(rst.proc_rank)

    px, py = proc_idx
    nxl, nyl = get_chunk_size(nx, ny)

    if include_overlap:
        sxl = 0 if px == 0 else 2
        sxu = nxl + 4 if (px + 1) == rs.num_proc[0] else nxl + 2
        syl = 0 if py == 0 else 2
        syu = nyl + 4 if (py + 1) == rs.num_proc[1] else nyl + 2
    else:
        sxl = syl = 0
        sxu = nxl
        syu = nyl

    global_slice, local_slice = [], []

    for dim in dim_grid:
        if dim in SCATTERED_DIMENSIONS[0]:
            global_slice.append(slice(sxl + px * nxl, sxu + px * nxl))
            local_slice.append(slice(sxl, sxu))
        elif dim in SCATTERED_DIMENSIONS[1]:
            global_slice.append(slice(syl + py * nyl, syu + py * nyl))
            local_slice.append(slice(syl, syu))
        else:
            global_slice.append(slice(None))
            local_slice.append(slice(None))

    return tuple(global_slice), tuple(local_slice)


def get_process_neighbors():
    this_x, this_y = proc_rank_to_index(rst.proc_rank)

    west = this_x - 1 if this_x > 0 else None
    south = this_y - 1 if this_y > 0 else None
    east = this_x + 1 if (this_x + 1) < rs.num_proc[0] else None
    north = this_y + 1 if (this_y + 1) < rs.num_proc[1] else None

    neighbors = [
        # direct neighbors
        (west, this_y),
        (this_x, south),
        (east, this_y),
        (this_x, north),
        # corners
        (west, south),
        (east, south),
        (east, north),
        (west, north),
    ]

    global_neighbors = [
        proc_index_to_rank(*i) if None not in i else None for i in neighbors
    ]
    return global_neighbors


@dist_context_only
def exchange_overlap(arr, var_grid):
    from veros.core.operators import numpy as np, update, at

    if len(var_grid) < 2:
        d1, d2 = var_grid[0], None
    else:
        d1, d2 = var_grid[:2]

    if d1 not in SCATTERED_DIMENSIONS[0] and d1 not in SCATTERED_DIMENSIONS[1] and d2 not in SCATTERED_DIMENSIONS[1]:
        # neither x nor y dependent, nothing to do
        return arr

    if d1 in SCATTERED_DIMENSIONS[0] and d2 in SCATTERED_DIMENSIONS[1]:
        proc_neighbors = get_process_neighbors()

        overlap_slices_from = (
            (slice(2, 4), slice(0, None), Ellipsis), # west
            (slice(0, None), slice(2, 4), Ellipsis), # south
            (slice(-4, -2), slice(0, None), Ellipsis), # east
            (slice(0, None), slice(-4, -2), Ellipsis), # north
            (slice(2, 4), slice(2, 4), Ellipsis), # south-west
            (slice(-4, -2), slice(2, 4), Ellipsis), # south-east
            (slice(-4, -2), slice(-4, -2), Ellipsis), # north-east
            (slice(2, 4), slice(-4, -2), Ellipsis), # north-west
        )

        overlap_slices_to = (
            (slice(0, 2), slice(0, None), Ellipsis), # west
            (slice(0, None), slice(0, 2), Ellipsis), # south
            (slice(-2, None), slice(0, None), Ellipsis), # east
            (slice(0, None), slice(-2, None), Ellipsis), # north
            (slice(0, 2), slice(0, 2), Ellipsis), # south-west
            (slice(-2, None), slice(0, 2), Ellipsis), # south-east
            (slice(-2, None), slice(-2, None), Ellipsis), # north-east
            (slice(0, 2), slice(-2, None), Ellipsis), # north-west
        )

        # flipped indices of overlap (n <-> s, w <-> e)
        send_to_recv = [2, 3, 0, 1, 6, 7, 4, 5]

    else:
        if d1 in SCATTERED_DIMENSIONS[0]:
            proc_neighbors = get_process_neighbors()[0:4:2] # west and east
        elif d1 in SCATTERED_DIMENSIONS[1]:
            proc_neighbors = get_process_neighbors()[1:4:2] # south and north
        else:
            raise NotImplementedError()

        overlap_slices_from = (
            (slice(2, 4), Ellipsis),
            (slice(-4, -2), Ellipsis),
        )

        overlap_slices_to = (
            (slice(0, 2), Ellipsis),
            (slice(-2, None), Ellipsis),
        )

        send_to_recv = [1, 0]

    for i_s, other_proc in enumerate(proc_neighbors):
        if other_proc is None:
            continue

        i_r = send_to_recv[i_s]
        recv_idx = overlap_slices_to[i_s]
        recv_arr = np.empty_like(arr[recv_idx])
        send_idx = overlap_slices_from[i_s]
        send_arr = arr[send_idx]

        recv_arr = sendrecv(
            send_arr, recv_arr,
            source=other_proc, dest=other_proc,
            sendtag=i_s, recvtag=i_r, comm=rs.mpi_comm
        )
        arr = update(arr, at[recv_idx], recv_arr)

    return arr


@dist_context_only
def exchange_cyclic_boundaries(arr):
    from veros.core.operators import numpy as np, update, at

    if rs.num_proc[0] == 1:
        arr = update(arr, at[-2:, ...], arr[2:4, ...])
        arr = update(arr, at[:2, ...], arr[-4:-2, ...])
        return arr

    ix, iy = proc_rank_to_index(rst.proc_rank)

    if 0 < ix < (rs.num_proc[0] - 1):
        return arr

    if ix == 0:
        other_proc = proc_index_to_rank(rs.num_proc[0] - 1, iy)
        send_idx = (slice(2, 4), Ellipsis)
        recv_idx = (slice(0, 2), Ellipsis)
    else:
        other_proc = proc_index_to_rank(0, iy)
        send_idx = (slice(-4, -2), Ellipsis)
        recv_idx = (slice(-2, None), Ellipsis)

    recv_arr = np.empty_like(arr[recv_idx])
    send_arr = arr[send_idx]

    recv_arr = sendrecv(
        send_arr, recv_arr,
        source=other_proc, dest=other_proc,
        sendtag=10, recvtag=10, comm=rs.mpi_comm
    )
    arr = update(arr, at[recv_idx], recv_arr)
    return arr


@dist_context_only
def _reduce(arr, op, axis=None):
    from veros.core.operators import numpy as np

    if axis is None:
        comm = rs.mpi_comm
        disconnect_comm = False
    else:
        # TODO: fix this
        assert axis in (0, 1)
        pi = proc_rank_to_index(rst.proc_rank)
        other_axis = 1 - axis
        comm = rs.mpi_comm.Split(pi[other_axis], rst.proc_rank)
        disconnect_comm = True

    try:
        if np.isscalar(arr):
            squeeze = True
            arr = np.array([arr])
        else:
            squeeze = False

        res = allreduce(arr, op=op, comm=rs.mpi_comm)

        if squeeze:
            res = res[0]

        return res

    finally:
        if disconnect_comm:
            comm.Disconnect()


@dist_context_only(noop_return_arg=0)
def global_and(arr, axis=None):
    from mpi4py import MPI
    return _reduce(arr, MPI.LAND, axis=axis)


@dist_context_only(noop_return_arg=0)
def global_or(arr, axis=None):
    from mpi4py import MPI
    return _reduce(arr, MPI.LOR, axis=axis)


@dist_context_only(noop_return_arg=0)
def global_max(arr, axis=None):
    from mpi4py import MPI
    return _reduce(arr, MPI.MAX, axis=axis)


@dist_context_only(noop_return_arg=0)
def global_min(arr, axis=None):
    from mpi4py import MPI
    return _reduce(arr, MPI.MIN, axis=axis)


@dist_context_only(noop_return_arg=0)
def global_sum(arr, axis=None):
    from mpi4py import MPI
    return _reduce(arr, MPI.SUM, axis=axis)


@dist_context_only(noop_return_arg=2)
def _gather_1d(nx, ny, arr, dim):
    from veros.core.operators import numpy as np, update, at

    assert dim in (0, 1)

    otherdim = 1 - dim
    pi = proc_rank_to_index(rst.proc_rank)
    if pi[otherdim] != 0:
        return arr

    dim_grid = ['xt' if dim == 0 else 'yt'] + [None] * (arr.ndim - 1)
    gidx, idx = get_chunk_slices(nx, ny, dim_grid, include_overlap=True)
    sendbuf = arr[idx]

    if rst.proc_rank == 0:
        buffer_list = []
        for proc in range(1, rst.proc_num):
            pi = proc_rank_to_index(proc)
            if pi[otherdim] != 0:
                continue
            idx_g, idx_l = get_chunk_slices(nx, ny, dim_grid, include_overlap=True, proc_idx=pi)
            recvbuf = np.empty_like(arr[idx_l])
            recvbuf = recv(recvbuf, source=proc, tag=20, comm=rs.mpi_comm)
            buffer_list.append((idx_g, recvbuf))

        out_shape = ((nx + 4, ny + 4)[dim],) + arr.shape[1:]
        out = np.empty(out_shape, dtype=arr.dtype)
        out = update(out, at[gidx], sendbuf)

        for idx, val in buffer_list:
            out = update(out, at[idx], val)

        return out

    else:
        send(sendbuf, dest=0, tag=20, comm=rs.mpi_comm)
        return arr


@dist_context_only(noop_return_arg=2)
def _gather_xy(nx, ny, arr):
    from veros.core.operators import numpy as np, update, at

    nxi, nyi = get_chunk_size(nx, ny)
    assert arr.shape[:2] == (nxi + 4, nyi + 4), arr.shape

    dim_grid = ['xt', 'yt'] + [None] * (arr.ndim - 2)
    gidx, idx = get_chunk_slices(nx, ny, dim_grid, include_overlap=True)
    sendbuf = arr[idx]

    if rst.proc_rank == 0:
        buffer_list = []
        for proc in range(1, rst.proc_num):
            idx_g, idx_l = get_chunk_slices(
                nx, ny, dim_grid, include_overlap=True,
                proc_idx=proc_rank_to_index(proc)
            )
            recvbuf = np.empty_like(arr[idx_l])
            recvbuf = recv(recvbuf, source=proc, tag=30, comm=rs.mpi_comm)
            buffer_list.append((idx_g, recvbuf))

        out_shape = (nx + 4, ny + 4) + arr.shape[2:]
        out = np.empty(out_shape, dtype=arr.dtype)
        out = update(out, at[gidx], sendbuf)

        for idx, val in buffer_list:
            out = update(out, at[idx], val)

        return out

    send(sendbuf, dest=0, tag=30, comm=rs.mpi_comm)
    return arr


@dist_context_only(noop_return_arg=2)
def gather(nx, ny, arr, var_grid):
    if len(var_grid) < 2:
        d1, d2 = var_grid[0], None
    else:
        d1, d2 = var_grid[:2]

    if d1 not in SCATTERED_DIMENSIONS[0] and d1 not in SCATTERED_DIMENSIONS[1] and d2 not in SCATTERED_DIMENSIONS[1]:
        # neither x nor y dependent, nothing to do
        return arr

    if d1 in SCATTERED_DIMENSIONS[0] and d2 not in SCATTERED_DIMENSIONS[1]:
        # only x dependent
        return _gather_1d(nx, ny, arr, 0)

    elif d1 in SCATTERED_DIMENSIONS[1]:
        # only y dependent
        return _gather_1d(nx, ny, arr, 1)

    elif d1 in SCATTERED_DIMENSIONS[0] and d2 in SCATTERED_DIMENSIONS[1]:
        # x and y dependent
        return _gather_xy(nx, ny, arr)

    else:
        raise NotImplementedError()


@dist_context_only(noop_return_arg=0)
def broadcast(obj):
    return rs.mpi_comm.bcast(obj, root=0)


@dist_context_only(noop_return_arg=0)
def _scatter_constant(arr):
    # TODO: use Bcast
    from veros.core.operators import numpy as np

    if rst.proc_rank == 0:
        for proc in range(1, rst.proc_num):
            send(arr, dest=proc, comm=rs.mpi_comm)
    else:
        arr = recv(np.empty_like(arr), source=0, comm=rs.mpi_comm)
    return arr


@dist_context_only(noop_return_arg=2)
def _scatter_1d(nx, ny, arr, dim):
    from veros.core.operators import numpy as np, update, at

    assert dim in (0, 1)

    out_nx = get_chunk_size(nx, ny)[dim]
    dim_grid = ['xt' if dim == 0 else 'yt'] + [None] * (arr.ndim - 1)
    _, local_slice = get_chunk_slices(nx, ny, dim_grid, include_overlap=True)

    if rst.proc_rank == 0:
        recvbuf = arr[local_slice]

        for proc in range(1, rst.proc_num):
            global_slice, _ = get_chunk_slices(nx, ny, dim_grid, include_overlap=True, proc_idx=proc_rank_to_index(proc))
            sendbuf = arr[global_slice]
            send(sendbuf, dest=proc, tag=40, comm=rs.mpi_comm)

        # arr changes shape in main process
        arr = np.zeros((out_nx + 4,) + arr.shape[1:], dtype=arr.dtype)
    else:
        recvbuf = recv(arr[local_slice], source=0, tag=40, comm=rs.mpi_comm)

    arr = update(arr, at[local_slice], recvbuf)
    arr = exchange_overlap(arr, ['xt' if dim == 0 else 'yt'])

    return arr


@dist_context_only(noop_return_arg=2)
def _scatter_xy(nx, ny, arr):
    from veros.core.operators import numpy as np, update, at

    nxi, nyi = get_chunk_size(nx, ny)

    dim_grid = ['xt', 'yt'] + [None] * (arr.ndim - 2)
    _, local_slice = get_chunk_slices(nx, ny, dim_grid, include_overlap=True)

    if rst.proc_rank == 0:
        recvbuf = arr[local_slice]

        for proc in range(1, rst.proc_num):
            global_slice, _ = get_chunk_slices(nx, ny, dim_grid, include_overlap=True, proc_idx=proc_rank_to_index(proc))
            sendbuf = arr[global_slice]
            send(sendbuf, dest=proc, tag=50, comm=rs.mpi_comm)

        # arr changes shape in main process
        arr = np.empty((nxi + 4, nyi + 4) + arr.shape[2:], dtype=arr.dtype)
    else:
        recvbuf = np.empty_like(arr[local_slice])
        recvbuf = recv(recvbuf, source=0, tag=50, comm=rs.mpi_comm)

    arr = update(arr, at[local_slice], recvbuf)
    arr = exchange_overlap(arr, ['xt', 'yt'])

    return arr


@dist_context_only(noop_return_arg=2)
def scatter(nx, ny, arr, var_grid):
    from veros.core.operators import numpy as np

    if len(var_grid) < 2:
        d1, d2 = var_grid[0], None
    else:
        d1, d2 = var_grid[:2]

    arr = np.asarray(arr)

    if d1 not in SCATTERED_DIMENSIONS[0] and d1 not in SCATTERED_DIMENSIONS[1] and d2 not in SCATTERED_DIMENSIONS[1]:
        # neither x nor y dependent
        return _scatter_constant(arr)

    if d1 in SCATTERED_DIMENSIONS[0] and d2 not in SCATTERED_DIMENSIONS[1]:
        # only x dependent
        return _scatter_1d(nx, ny, arr, 0)

    elif d1 in SCATTERED_DIMENSIONS[1]:
        # only y dependent
        return _scatter_1d(nx, ny, arr, 1)

    elif d1 in SCATTERED_DIMENSIONS[0] and d2 in SCATTERED_DIMENSIONS[1]:
        # x and y dependent
        return _scatter_xy(nx, ny, arr)

    else:
        raise NotImplementedError()


@dist_context_only
def barrier():
    rs.mpi_comm.barrier()


@dist_context_only
def abort():
    rs.mpi_comm.Abort()
