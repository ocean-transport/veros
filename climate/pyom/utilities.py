import numpy as np

import climate.pyom.numerics

def pad_z_edges(array):
    """
    Pads the z-axis of an array by repeating its edge values
    """
    if array.ndim == 1:
        newarray = np.empty(array.shape[0] + 2)
        newarray[1:-1] = array
        newarray[0] = array[0]
        newarray[-1] = array[-1]
    elif array.ndim >= 3:
        a = list(array.shape)
        a[2] += 2
        newarray = np.empty(a)
        newarray[:,:,1:-1,...] = array
        newarray[:,:,0,...] = array[:,:,0,...]
        newarray[:,:,-1,...] = array[:,:,-1,...]
    else:
        raise ValueError("Array to pad needs to have 1 or at least 3 dimensions")
    return newarray


def solve_implicit(ks, a, b, c, d, b_edge=None, d_edge=None):
    land_mask = (ks >= 0)[:,:,None]
    if not np.any(land_mask):
        return np.zeros_like(land_mask), np.zeros_like(land_mask)
    edge_mask = land_mask & (np.indices((a.shape))[2] == ks[:,:,None])
    water_mask = land_mask & (np.indices((a.shape))[2] >= ks[:,:,None])

    a_tri = np.where(water_mask, a, 0.)
    a_tri = np.where(edge_mask, 0., a_tri)
    b_tri = np.where(water_mask, b, 1.)
    if not (b_edge is None):
        b_tri = np.where(edge_mask, b_edge, b_tri)
    c_tri = np.where(water_mask, c, 0.)
    c_tri[:,:,-1] = 0.
    d_tri = np.where(water_mask, d, 0.)
    if not (d_edge is None):
        d_tri = np.where(edge_mask, d_edge, d_tri)
    return climate.pyom.numerics.solve_tridiag(a_tri,b_tri,c_tri,d_tri), water_mask
