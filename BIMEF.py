import numpy as np
import cv2
import time
import pyamg
from imresize import imresize
from sksparse.cholmod import cholesky
from scipy import signal
from scipy.sparse import spdiags
from scipy.optimize import fminbound
from scipy.stats import entropy


def BIMEF(I, mu=0.5, k=None, a=-0.3293, b=1.1258):
    """
    :param I:   image data (of an RGB image) stored as a 3D numpy array (height x width x color)
    :param mu:  enhancement ratio
    :param k:   exposure ratio (array)
    :param a:   camera response model parameter
    :param b:   camera response model parameter
    :return:    fused: enhanced result
    """

    def maxEntropyEnhance(I, isBad=None):
        Y = rgb2gm(np.real(np.maximum(imresize(I, output_shape=(50, 50)), 0)))
        import pdb;pdb.set_trace()

        if not (isBad is None):
            isBad = imresize(isBad, output_shape=(50, 50)).T  # why is there a transpose here?
            Y = Y[isBad]
            Y = np.reshape(Y, (Y.size, 1), order='F')  # why is there a reshape here?

        if Y.size == 0:
            J = I
            return J

        _, opt_k, _, _ = fminbound(lambda k: -entropy(cv2.calcHist([applyK(Y, k)], [0], None, [256], [0, 1])),
                                   x1=1, x2=7)
        J = applyK(I, opt_k, a, b) - 0.01

        return J

    I0 = I
    I = im2double(I)

    lamb = 0.5
    sigma = 5

    # t: scene illumination map
    t_b = np.amax(I, axis=2)
    t_our = imresize(tsmooth(imresize(t_b, scalar_scale=0.5), lamb, sigma), output_shape=t_b.shape)
    # We try to replicate MatLab's imresize function, which uses intercubic interpolation and anti-aliasing by default

    # k: exposure ratio
    if k is None or k.size == 0:  # this path is taken
        isBad = t_our < 0.5  # compare t_our to 0.5 element-wise and creates a new array of truth values
        J = maxEntropyEnhance(I, isBad)
    else:
        J = applyK(I, k, a, b)
        J = np.amin(J, axis=0)
        # remember to check this!

    # W: Weight Matrix
    t = np.tile(t_our, [1, 1, np.shape(I)[2]])
    W = t**mu
    I2 = I**W
    J2 = I**(1-W)
    fused = I2+J2

    return fused


def rgb2gm(I):
    if np.shape(I)[2] == 3:
        I = im2double(np.maximum(0, I))
        I = (I[:, :, 0] * I[:, :, 1] * I[:, :, 2]) ** (1.0/3.0)
    import pdb;pdb.set_trace()
    return I


def applyK(I, k, a=-0.3293, b=1.1258):
    f = lambda x: np.exp((1-x**a)*b)
    beta = f(k)
    gamma = k**a
    J = I**gamma*beta
    return J


def tsmooth(I, lamb=0.01, sigma=3.0, sharpness=0.001):
    I = im2double(I)
    x = I
    wx, wy = computeTextureWeights(x, sigma, sharpness)
    S = solveLinearEquation(I, wx, wy, lamb)
    S = np.squeeze(S)
    return S


def computeTextureWeights(fin, sigma, sharpness):
    v1 = np.diff(fin, axis=0)
    v2 = np.expand_dims(fin[0, :] - fin[-1, :], axis=0)
    dt0_v = np.concatenate((v1, v2), axis=0)
    h1 = np.matrix(np.diff(fin, axis=1)).H
    h2 = np.matrix(np.expand_dims(fin[:, 0], axis=1)).H - np.matrix(np.expand_dims(fin[:, -1], axis=1)).H
    dt0_h = np.matrix(np.concatenate((h1, h2), axis=0)).H

    gauker_h = signal.convolve2d(dt0_h, np.ones((1, sigma)), mode='same')
    gauker_v = signal.convolve2d(dt0_v, np.ones((sigma, 1)), mode='same')
    W_h = np.multiply(np.absolute(gauker_h), np.absolute(dt0_h)) + sharpness
    W_h = np.divide(1, W_h)
    W_v = np.multiply(np.absolute(gauker_v), np.absolute(dt0_v)) + sharpness
    W_v = np.divide(1, W_v)

    return W_h, W_v


def solveLinearEquation(IN, wx, wy, lamb):
    if len(IN.shape) == 2:
        IN = np.expand_dims(IN, axis=2)
    r, c, ch = IN.shape
    k = r * c
    dx = -lamb * np.reshape(wx, (wx.size, 1), order='F')
    dy = -lamb * np.reshape(wy, (wy.size, 1), order='F')
    tempx = np.concatenate((wx[:, -1], wx[:, 0:-1]), axis=1)
    tempy = np.concatenate((np.expand_dims(wy[-1, :], axis=0), wy[0:-1, :]), axis=0)
    dxa = -lamb * np.reshape(tempx, (tempx.size, 1), order='F')
    dya = -lamb * np.reshape(tempy, (tempy.size, 1), order='F')
    tempx = np.concatenate((wx[:, -1], np.zeros((r, c-1))), axis=1)
    tempy = np.concatenate((np.expand_dims(wy[-1, :], axis=0), np.zeros((r-1, c))), axis=0)
    dxd1 = -lamb * np.reshape(tempx, (tempx.size, 1), order='F')
    dyd1 = -lamb * np.reshape(tempy, (tempy.size, 1), order='F')
    wx[:, -1] = 0
    wy[-1, :] = 0
    dxd2 = -lamb * np.reshape(wx, (wx.size, 1), order='F')
    dyd2 = -lamb * np.reshape(wy, (wy.size, 1), order='F')

    Ax = spdiags(np.concatenate((dxd1, dxd2), axis=1).T, [-k+r, -r], k, k)
    Ay = spdiags(np.concatenate((dyd1, dyd2), axis=1).T, [-r+1, -1], k, k)
    # diagonals stored row-wise; in MatLab the diagonals are stored column-wise

    D = 1 - (dx + dy + dxa + dya)  # column vector

    Axy = Ax + Ay
    A = Axy + Axy.T + spdiags(D.T, 0, k, k)

    fast = True
    if fast:
        OUT = IN
        for ii in range(ch):
            tin = IN[:, :, ii]
            tin = np.reshape(tin, (tin.size, 1), order='F')
            # start_amg = time.time()
            # ml = pyamg.ruge_stuben_solver(A)
            # tout = ml.solve(tin)
            # end_amg = time.time()
            # time_amg = end_amg-start_amg
            # print(time_amg)

            # run time of 1.04663395882 seconds

            start_cholmod = time.time()
            factor = cholesky(A)
            tout = factor(tin)
            end_cholmod = time.time()
            time_cholmod = end_cholmod-start_cholmod
            print(time_cholmod)

            # run time of 0.731502056122 seconds
            OUT[:, :, ii] = np.reshape(tout, (r, c), order='F')  # matches the A\tin(:), not the ichol from matlab
    else:
        # Solving A*x = tin is extremely slow here
        OUT = IN
        for ii in range(ch):
            tin = IN[:, :, ii]
            tin = np.reshape(tin, (tin.size, 1), order='F')
            tout = np.linalg.lstsq(A.toarray(), tin)
            OUT[:, :, ii] = np.reshape(tout, (r, c), order='F')

    return OUT


def im2double(im):
    if im.dtype == np.float64:
        return im  # do nothing if the image array has already been converted to floating points
    else:
        info = np.iinfo(im.dtype)  # Get the data type of the input image
        return im.astype(np.float64) / info.max  # Divide all values by the largest possible value in the datatype
