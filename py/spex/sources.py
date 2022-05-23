#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPEX - SPectra EXtractor.

This module provides utility to identify sources directly from datacubes.

Copyright (C) 2022  Maurizio D'Addona <mauritiusdadd@gmail.com>
"""
from __future__ import absolute_import, division, print_function

import os
import gc
import sys
import argparse
import numpy as np
import multiprocessing as mp

from astropy import wcs
from astropy.io import fits
from astropy.table import Table
from astropy.convolution import Gaussian2DKernel, convolve

from scipy.ndimage import maximum_filter
from astropy.convolution import Gaussian2DKernel, convolve

from .utils import get_pbar, get_hdu, get_peaks
from .utils import get_spectrum_snr, get_spectrum_snr_emission


def __argshandler(options=None):
    """
    Parse the arguments given by the user.

    Returns
    -------
    args: Namespace
        A Namespace containing the parsed arguments. For more information see
        the python documentation of the argparse module.
    """
    parser = argparse.ArgumentParser(
        description='Identify sources directly from the datacube.'
    )
    parser.add_argument(
        'input_cube', metavar='SPEC_CUBE', type=str, nargs=1,
        help='The spectral cube in fits format from which spectra will be '
        'extracted.'
    )

    parser.add_argument(
        '--out-regionfile', metavar='REGFILE', type=str, default=None,
        help='The path of a region file where to save identified sources'
    )

    parser.add_argument(
        '--sn-threshold', metavar='SN_THRESH', type=float, default=1.5,
        help='Set the signal to Noise ratio threshold: objects with their '
        'spectrum having a SN ratio lower than threshold will be ignored.'
    )

    parser.add_argument(
        '--info-hdu', metavar='INFO_HDU', type=int, default=0,
        help='The HDU containing cube metadata. If this argument '
        'Set this to -1 to automatically detect the HDU containing the info. '
        'NOTE that this value is zero indexed (i.e. firts HDU has index 0).'
    )

    parser.add_argument(
        '--spec-hdu', metavar='SPEC_HDU', type=int, default=-1,
        help='The HDU containing the spectral data to use. If this argument '
        'Set this to -1 to automatically detect the HDU containing spectra. '
        'NOTE that this value is zero indexed (i.e. second HDU has index 1).'
    )

    parser.add_argument(
        '--var-hdu', metavar='VAR_HDU', type=int, default=-1,
        help='The HDU containing the variance of the spectral data. '
        'Set this to -1 if no variance data is present in the cube. '
        'The default value is %(metavar)s=%(default)s.'
        'NOTE that this value is zero indexed (i.e. third HDU has index 2).'
    )

    parser.add_argument(
        '--mask-hdu', metavar='MASK_HDU', type=int, default=-1,
        help='The HDU containing the valid pixel mask of the spectral data. '
        'Set this to -1 if no mask is present in the cube. '
        'The default value is %(metavar)s=%(default)s.'
        'NOTE that this value is zero indexed (i.e. fourth HDU has index 3).'
    )

    parser.add_argument(
        '--invert-mask', action='store_true', default=False,
        help='Set whether to use the inverse of the cube mask.'
    )

    parser.add_argument(
        '--outdir', metavar='DIR', type=str, default=None,
        help='Set the directory where extracted spectra will be outputed. '
        'If this parameter is not specified, then a new directory will be '
        'created based on the name of the input cube.'
    )

    args = None
    if options is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(options)

    return args


def rebin_slice_2d(data, bin_size_x, bin_size_y=None, rebin_function=np.mean):
    """
    Rebin a 2D image.

    Parameters
    ----------
    data : 2D np.ndarray
        The input array.
    bin_size_x : int
        Number of pixels to combine along x axis.
    bin_size_y : int, optional
        Number of pixels to combine along x axis. If none, use the same value
        as bin_size_x The default is None.
    rebin_function : function, optional
        The function used to combine pixels. The default is np.mean.

    Returns
    -------
    rebinned : 2D np.ndarray
        The rebinned array.

    """
    bin_size_x = int(bin_size_x)

    if bin_size_y is None:
        bin_size_y = bin_size_x
    else:
        bin_size_y = int(bin_size_y)

    if bin_size_x <= 0:
        raise ValueError("bin_size_x should be greater than zero!")

    if bin_size_y <= 0:
        raise ValueError("bin_size_y should be greater than zero!")

    if bin_size_x == 1 and bin_size_y == 1:
        raise ValueError(
            "At least one between bin_size_x and bin_size_y should be greater"
            " than one!"
        )

    rebinned_h = data.shape[0] // bin_size_y
    rebinned_w = data.shape[1] // bin_size_x

    # Resize data to optimal height and width for rebinning.
    if data.shape[0] % bin_size_y:
        rebinned_h += 1
        optimal_data_height = rebinned_h * bin_size_y
    else:
        optimal_data_height = data.shape[0]

    if data.shape[1] % bin_size_x:
        rebinned_w += 1
        optimal_data_width = rebinned_w * bin_size_x
    else:
        optimal_data_width = data.shape[1]

    # Padding with original array in order to reshape it correctly
    zeros = np.zeros((optimal_data_height, optimal_data_width))
    masked_data = np.ma.array(zeros, mask=zeros == 0)
    masked_data.mask[:data.shape[0], :data.shape[1]] = False
    masked_data[:data.shape[0], :data.shape[1]] = data

    # Reshape the array
    reshaped = masked_data.reshape(
        rebinned_h, bin_size_y,
        rebinned_w, bin_size_x
    ).transpose(0, 2, 1, 3)

    reshaped = reshaped.reshape(rebinned_h, rebinned_w, bin_size_y*bin_size_x)

    # Do actual rebinning
    rebinned = rebin_function(reshaped, axis=-1)

    return rebinned


def get_sn_single_slice(cube_slice, cube_slice_var=None, rebin_size=2,
                        kernel_width=1):
    """
    Get Signal to Noise ratio of an image.

    Identify spotential ources from a single 2D array (image) considering the
    signal to noise ratio of each pixel. If a pixel has a SN greather than the
    given threshold then it is considered to belong to a source.

    NOTE
    ----
    This function is *very* memory intensive and will probably never be
    actually used since I wrote more memory-wise functions. I leave it
    here just as an example.

    Parameters
    ----------
    cube_slice : 2D np.ndarray
        A single slice of the datacube.
    cube_slice_var : 2D np.ndarray, optional
        The variance map for the input slice. If this is not provided,
        noise will be approximately estimated directly from the input slice.
    noise_rebin_size : int, optional
        Both imput slice and  cube_slice_var (if provided) are rebinned
        combining NxN pixel into one pixel, where N = 2*rebin_size + 1.
    kernel_width : float:
        The std. dev. to be passed to the smoothing gaussian kernel.
        (For more info see astropy.convolution.Gaussian2DKernel)

    Returns
    -------
    None.

    """
    # Compute the SN ratio for each pixel in the slice.
    # First of all, smooth the image using a 2D gaussian kernel.
    kernel = Gaussian2DKernel(x_stddev=kernel_width)  # x_stddev = y_stddev
    smoothed_slice = convolve(
        cube_slice,
        kernel,
        nan_treatment='fill',
        fill_value='-1',
        preserve_nan=True
    )

    bin_size = 2 * rebin_size + 1

    if cube_slice_var is None:
        # No variance information, try to perform a crude estimation of the
        # noise by subtracting the smoothed image from the original image
        noise_image = cube_slice - smoothed_slice

        # Rebinning to get variance
        rebinned_var = rebin_slice_2d(
            noise_image, bin_size, rebin_function=np.var
        )

        # Rescale back to original size
        cube_slice_var = np.kron(rebinned_var, np.ones((bin_size, bin_size)))
        cube_slice_var = cube_slice_var[
            :cube_slice.shape[0],
            :cube_slice.shape[1]
        ]

    sn_map = smoothed_slice / np.sqrt(cube_slice_var)

    return sn_map


def get_snr_spaxels(data_hdu, var_hdu=None, mask_hdu=None, inverse_mask=False,
                    snr_function=get_spectrum_snr):
    """
    Get the SNR map of a spectral datacube.

    Parameters
    ----------
    data_hdu : astropy.fits.ImageHDU
        The HDU containing spectral data.
    mask_hdu : astropy.fits.ImageHDU, optional
        The HDU containing data qaulity mask. The default is None.
    inverse_mask : bool, optional
        Wether. The default is False.

    Returns
    -------
    snr_map : TYPE
        DESCRIPTION.

    """

    if mask_hdu is None:
        cube_mask = np.isnan(data_hdu.data)
    else:
        cube_mask = mask_hdu.data != 0 if inverse_mask else mask_hdu.data == 0

    cube_var = var_hdu.data if var_hdu is not None else None

    cube_flux = np.ma.array(data_hdu.data, mask=cube_mask)

    snr_map = np.zeros((cube_flux.shape[1], cube_flux.shape[2]))

    i = 0
    footprint_size = cube_flux.shape[1] * cube_flux.shape[2]
    results = {}
    # Generating jobs...
    with mp.Pool() as my_pool:
        for h in range(cube_flux.shape[1]):
            for k in range(cube_flux.shape[2]):
                i += 1
                if (i % 10) == 0:
                    progress = 0.5 * i / footprint_size
                    sys.stderr.write(
                        f"\r{get_pbar(progress)} {progress:.2%}\r"
                    )
                    sys.stderr.flush()
                # The snr of a single spaxel
                # snr_map[h, k] = get_spectrum_snr(cube_flux[:, h, k])
                results[h, k] = my_pool.apply_async(
                    snr_function,
                    (
                        cube_flux[:, h, k],
                        cube_var[:, h, k] if cube_var is not None else None
                    ),
                )

        for i, (pos, val) in enumerate(results.items()):
            if (i % 10) == 0:
                progress = 0.5 + 0.5 * i / footprint_size
                sys.stderr.write(
                    f"\r{get_pbar(progress)} {progress:.2%}\r"
                )
                sys.stderr.flush()
            val.wait()
            snr_map[pos] = val.get()
    print("")
    return snr_map


def get_peaks(image, box_sizes=[3, 5], threshold=0, smoothing_factor=1):
    """
    Find local peaks in an image.

    Parameters
    ----------
    image : 2D numpy.ndarray
        The image data.
    box_size : int, optional
        The size of the dilation box. The default is 3.
        See https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.maximum_filter.html
    threshold : float, optional
        Peaks with value lower or equal than this value will be discarded.
    smoothing_factor : float, optional
        Std. Dev. of the gaussian kernel used to smooth the input image.

    Returns
    -------
    peaks : list
        list of 2-tuples containing the positions of the local maxima.

    """

    if not box_sizes:
        box_sizes = [3, 5]

    smoothing_kernel = Gaussian2DKernel(x_stddev=1)
    im_smooth = convolve(
        image,
        smoothing_kernel,
        preserve_nan=True
    )

    dilated = [
        maximum_filter(image, size=x) for x in box_sizes
    ]

    mask = np.ones_like(image, dtype=bool)

    for d_img in dilated[1:]:
        mask &= im_smooth == d_img

    peaks = np.argwhere(mask)

    valid_peaks = np.array(
        (x[0], x[1], im_smooth[x[0], x[1]]) for x in peaks
        if im_smooth[x[0], x[1]] > 0.0
    )

    tbl = Table(data=valid_peaks, names=['X_IMAGE', 'Y_IMAGE', 'SNR'])

    return tbl


def detect_from_cube(options=None):
    """
    Run the main program of this module.

    Parameters
    ----------
    options : list, optional
        List of arguments to pass to argparse. The default is None.

    Returns
    -------
    None.

    """
    args = __argshandler(options)

    basename_with_ext = os.path.basename(args.input_cube[0])
    basename = os.path.splitext(basename_with_ext)[0]

    hdl = fits.open(args.input_cube[0])

    spec_hdu = get_hdu(
        hdl,
        hdu_index=args.spec_hdu,
        valid_names=['data', 'spec', 'spectrum', 'spectra'],
        msg_err_notfound="ERROR: Cannot determine which HDU contains spectral "
                         "data, try to specify it manually!",
        msg_index_error="ERROR: Cannot open HDU {} to read specra!"
    )

    var_hdu = get_hdu(
        hdl,
        hdu_index=args.var_hdu,
        valid_names=['stat', 'var', 'variance', 'noise'],
        msg_err_notfound="WARNING: Cannot determine which HDU contains the "
                         "variance data, try to specify it manually!",
        msg_index_error="WARNING: Cannot open HDU {} to read the "
                        "variance!",
        exit_on_errors=False
    )

    mask_hdu = get_hdu(
        hdl,
        hdu_index=args.mask_hdu,
        valid_names=['mask', 'platemask', 'footprint', 'dq'],
        msg_err_notfound="WARNING: Cannot determine which HDU contains the "
                         "mask data, try to specify it manually!",
        msg_index_error="WARNING: Cannot open HDU {} to read the mask!",
        exit_on_errors=False
    )

    cube_wcs = wcs.WCS(spec_hdu.header)
    celestial_wcs = cube_wcs.celestial

    """
    snr_map = get_snr_map(spec_hdu, var_hdu, mask_hdu)
    snr_map = snr_map.filled(fill_value=np.nan)
    hdu = fits.PrimaryHDU(data=snr_map, header=celestial_wcs.to_header())
    hdu.writeto(f"{basename}_snr_map.fits", overwrite=True)
    """

    print("\nComputing SNR of emission fearures...", file=sys.stderr)
    snr_map_em = get_snr_spaxels(
        spec_hdu, var_hdu, mask_hdu,
        snr_function=get_spectrum_snr_emission
    )
    hdu = fits.PrimaryHDU(data=snr_map_em, header=celestial_wcs.to_header())
    hdu.writeto(f"{basename}_snr_map_em.fits", overwrite=True)

    print("\nComputing SNR of the continuum...", file=sys.stderr)
    snr_map_ct = get_snr_spaxels(
        spec_hdu, var_hdu, mask_hdu,
        snr_function=get_spectrum_snr
    )
    hdu = fits.PrimaryHDU(data=snr_map_ct, header=celestial_wcs.to_header())
    hdu.writeto(f"{basename}_snr_map_cont.fits", overwrite=True)

    smoothing_kernel = Gaussian2DKernel(x_stddev=1.5)

    snr_map_em_smooth = convolve(
        snr_map_em,
        smoothing_kernel,
        preserve_nan=True
    )
    snr_map_ct_smooth = convolve(
        snr_map_ct,
        smoothing_kernel,
        preserve_nan=True
    )

    snr_map_em_smooth[snr_map_em_smooth < 0] = 0
    snr_map_ct_smooth[snr_map_ct_smooth < 0] = 0

    snr_map = snr_map_em_smooth + snr_map_ct_smooth
    hdu = fits.PrimaryHDU(data=snr_map, header=celestial_wcs.to_header())
    hdu.writeto(f"{basename}_snr_map_total.fits", overwrite=True)