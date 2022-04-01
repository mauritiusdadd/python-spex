#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPEX - SPectra EXtractor.

Extract spectra from spectral data cubes.

Created on Tue Mar 29 11:46:39 2022.

@author: Maurizio D'Addona <mauritiusdadd@gmail.com>
"""
import os
import sys
import argparse

import numpy as np
from astropy import units as u
from astropy import wcs
from astropy.table import Table
from astropy.io import fits
from astropy.coordinates import SkyCoord
from astropy.time import Time

import matplotlib.pyplot as plt


def getpbar(partial, total=None, wid=32, common_char='\u2588',
            upper_char='\u2584', lower_char='\u2580'):
    """
    Return a nice text/unicode progress bar showing partial and total progress.

    Parameters
    ----------
    partial : float
        Partial progress expressed as decimal value.
    total : float, optional
        Total progress expresses as decimal value.
        If it is not provided or it is None, than
        partial progress will be shown as total progress.
    wid : TYPE, optional
        Width in charachters of the progress bar.
        The default is 32.

    Returns
    -------
    TYPE
        DESCRIPTION.

    """
    wid -= 2
    prog = int((wid)*partial)
    if total is None:
        total_prog = prog
        common_prog = prog
    else:
        total_prog = int((wid)*total)
        common_prog = min(total_prog, prog)
    pbar_full = common_char*common_prog
    pbar_full += upper_char*(total_prog - common_prog)
    pbar_full += lower_char*(prog - common_prog)
    return (f"\u2595{{:<{wid}}}\u258F").format(pbar_full)


def argshandler():
    """
    Parse the arguments given by the user.

    Returns
    -------
    args: Namespace
        A Namespace containing the parsed arguments. For more information see
        the python documentation of the argparse module.
    """
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument(
        'input_cube', metavar='SPEC_CUBE', type=str, nargs=1,
        help='The spectral cube in fits format from which spectra will be '
        'extracted.'
    )

    parser.add_argument(
        '--mode', metavar='WORKING_MODE', type=str, default='auto',
        help='Set the working mode of the program. Can be "auto", '
        '"kron'
    )

    parser.add_argument(
        '--boss', action='store_true', default=False,
        help='Write extracted spectra as BOSS spPlate fits instead of a single'
        'spectrum for each object.'
    )

    parser.add_argument(
        '--catalog', metavar='CATALOG', type=str, default=None,
        help='A catalog containing the position and the (optional) morphology '
        'of the objects of which you want to extract the spectra. The catalog '
        'must contain at least the RA and DEC coordinates and optionally the '
        'dimensions of the isophotal axes and the rotation angle of the '
        ' isophotal ellipse, as well as the kron radius. The way how these '
        'parameter are used depends on the program working mode (see the '
        '--mode section for more information). The catalog format is'
        ' by default compatible by the one generated by Sextractor, so the '
        'column name used to read the aformentioned qauntities are '
        'respectively ALPHA_J2000, DELTA_J2000, A_IMAGE, B_IMAGE, THETA_IMAGE '
        'and KRON_RADIUS. If the catalog has different column names for these '
        'quantities, they can be overridden with a corresponding --key-* '
        'argument (see below).'
    )

    parser.add_argument(
        '--spec-hdu', metavar='SPEC_HDU', type=int, default=1,
        help='The HDU containing the spectral data to use. If this argument '
        'is not specified, the image is loaded from the first HDU.'
    )

    parser.add_argument(
        '--var-hdu', metavar='VAR_HDU', type=int, default=2,
        help='The HDU containing the variance of the spectral data. '
        'The default value is %(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--mask-hdu', metavar='MASK_HDU', type=int, default=3,
        help='The HDU containing the valid pixel mask of the spectral data. '
        'The default value is %(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--invert-mask', action='store_true', default=False,
        help='Set whether to use the inverse of the cube mask.'
    )

    parser.add_argument(
        '--no-nans', action='store_true', default=False,
        help='Set whether to remove NaNs from the spectrum. If this argument '
        'is specified, then the extracted spectrum is filtered and NaNs are '
        'set to zero. If variance extension is '
        'present, the variances corresponding to NaNs are set to very high '
        'values.'
    )

    parser.add_argument(
        '--aperture-size', metavar='APERTURE_MAS', type=float, default=0.8,
        help='Set the radius in mas of a fixed circula aperture used in the '
        '"fixed-aperture" mode. The pixels within this aperture will be used '
        'for spectra extraction. The default value is %(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-ra', metavar='RA_KEY', type=str, default='ALPHA_J2000',
        help='Set the name of the column from which to read the RA of the '
        'objects. The default value is %(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-dec', metavar='DEC_KEY', type=str, default='DELTA_J2000',
        help='Set the name of the column from which to read the RA of the '
        'objects. The default value is %(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-a', metavar='A_KEY', type=str, default='A_IMAGE',
        help='Set the name of the column from which to read the size of the'
        'semi-major isophotal axis. The default value is '
        '%(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-b', metavar='B_KEY', type=str, default='B_IMAGE',
        help='Set the name of the column from which to read the size of the'
        'semi-minor isophotal axis. The default value is '
        '%(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-theta', metavar='THETA_KEY', type=str, default='THETA_IMAGE',
        help='Set the name of the column from which to read the rotation angle'
        ' of the isophotal ellipse. The default value is '
        '%(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--key-kron', metavar='KRON_KEY', type=str, default='KRON_RADIUS',
        help='Set the name of the column from which to read the size of the '
        'Kron radius (in units of A or B). The default value is '
        '%(metavar)s=%(default)s.'
    )

    parser.add_argument(
        '--outdir', metavar='DIR', type=str, default=None,
        help='Set the directory where extracted spectra will be outputed. '
        'If this parameter is not specified, then a new directory will be '
        'created based on the name of the input cube.'
    )

    parser.add_argument(
        '--check-images', action='store_true', default=False,
        help='Wheter or not to generate check images (cutouts, etc.).'
    )

    return parser.parse_args()


def getspsinglefits(main_header, spec_wcs_header, obj_spectrum,
                    spec_hdu_header, obj_spectrum_var=None):
    """
    Generate a fits containing the spectral data.

    Parameters
    ----------
    main_header : dict
        Dictionary containing the base information of the spectrum extraction.
        The keys of the dictionaries are the names of the fits CARDS that will
        be written, while the values can be either strings or tuples in the
        form of (cadr value, card comment).
    spec_wcs_header : astropy.io.fits.Header object
        Header containing the WCS information of the spectrum.
    obj_spectrum : numpy.ndarray
        The actual spectral data.
    spec_hdu_header : astropy.io.fits.Header object
        Header of the HDU of the original cube containing the spectral data.
    obj_spectrum_var : numpy.ndarray (default=None)
        The variance of the spectral data.

    Returns
    -------
    hdul : astropy.io.fits.HDUList object
        The fits HDUL containing the extracted spectra.

    """
    my_time = Time.now()
    my_time.format = 'isot'

    # The primary hdu contains no data but a header with information on
    # how the spectrum was extracted
    main_hdu = fits.PrimaryHDU()
    for key, val in main_header.items():
        main_hdu.header[key] = val

    spec_header = fits.Header()
    spec_header['BUNIT'] = spec_hdu_header['BUNIT']
    spec_header["OBJECT"] = spec_hdu_header["OBJECT"]
    spec_header["CUNIT1"] = spec_hdu_header["CUNIT1"]
    spec_header["CTYPE1"] = spec_hdu_header["CTYPE1"]
    spec_header["OBJECT"] = spec_hdu_header["OBJECT"]
    spec_header["CRPIX1"] = spec_hdu_header["CRPIX3"]
    spec_header["CRVAL1"] = spec_hdu_header["CRVAL3"]
    spec_header["CDELT1"] = spec_hdu_header["CD3_3"]
    spec_header["OBJECT"] = spec_hdu_header["OBJECT"]

    hdu_list = [
        main_hdu,
        fits.ImageHDU(
            data=obj_spectrum,
            header=spec_header,
            name='SPECTRUM'
        )
    ]

    if obj_spectrum_var is not None:
        var_header = fits.Header()
        var_header['BUNIT'] = spec_hdu_header['BUNIT']
        var_header["OBJECT"] = spec_hdu_header["OBJECT"]
        var_header["CUNIT1"] = spec_hdu_header["CUNIT1"]
        var_header["CTYPE1"] = spec_hdu_header["CTYPE1"]
        var_header["OBJECT"] = spec_hdu_header["OBJECT"]
        var_header["CRPIX1"] = spec_hdu_header["CRPIX3"]
        var_header["CRVAL1"] = spec_hdu_header["CRVAL3"]
        var_header["CDELT1"] = spec_hdu_header["CD3_3"]
        var_header["OBJECT"] = spec_hdu_header["OBJECT"]
        hdu_list.append(
            fits.ImageHDU(
                data=obj_spectrum_var,
                header=var_header,
                name='VARIANCE'
            )
        )

    hdul = fits.HDUList(hdu_list)
    return hdul


def main():
    """
    Run the main program.

    Returns
    -------
    None.

    """
    args = argshandler()

    # Get user input from argument parsers helper

    basename_with_ext = os.path.basename(args.catalog)
    basename = os.path.splitext(basename_with_ext)[0]

    sources = Table.read(args.catalog)

    hdl = fits.open(args.input_cube[0])

    spec_hdu = hdl[args.spec_hdu]
    var_hdu = hdl[args.var_hdu]
    mask_hdu = hdl[args.mask_hdu]

    img_wcs = wcs.WCS(spec_hdu.header)
    celestial_wcs = img_wcs.celestial
    spectral_wcs = img_wcs.spectral
    wcs_frame = wcs.utils.wcs_to_celestial_frame(img_wcs)

    obj_sky_coords = SkyCoord(
        sources[args.key_ra], sources[args.key_dec],
        unit=(u.deg, u.deg),
        frame=wcs_frame
    )

    obj_x, obj_y = wcs.utils.skycoord_to_pixel(
        coords=obj_sky_coords,
        wcs=celestial_wcs
    )

    sources.add_column(obj_x, name='MUSE_X_IMAGE')
    sources.add_column(obj_y, name='MUSE_Y_IMAGE')

    img_height, img_width = spec_hdu.data.shape[1], spec_hdu.data.shape[2]

    yy, xx = np.meshgrid(
        np.arange(img_height),
        np.arange(img_width),
        indexing='ij'
    )

    if args.check_images:
        extracted_data = np.zeros((img_height, img_width))

    trasposed_spec = spec_hdu.data.transpose(1, 2, 0)

    if var_hdu is not None:
        trasposed_var = var_hdu.data.transpose(1, 2, 0)

    if mask_hdu is not None:
        cube_footprint = mask_hdu.data.prod(axis=0) == 0
        if args.invert_mask:
            cube_footprint = ~cube_footprint

    else:
        cube_footprint = None

    if args.outdir is None:
        outdir = f'{basename}_spectra'
    else:
        outdir = args.outdir

    if not os.path.isdir(outdir):
        os.mkdir(outdir)

    if args.mode == 'auto':
        if (
                args.key_a in sources.colnames and
                args.key_b in sources.colnames and
                args.key_kron in sources.colnames
        ):
            mode = 'kron_ellipse'
        else:
            mode = 'circular_aperture'
    else:
        mode = args.mode

    print(f"Extracting spectra with strategy '{mode}'", file=sys.stderr)

    # TODO: add region file handling
    for i, source in enumerate(sources):
        progress = i / len(sources)
        sys.stderr.write(f"\r{getpbar(progress)} {progress:.2%}\r")
        sys.stderr.flush()
        obj_ra = source[args.key_ra]
        obj_dec = source[args.key_dec]
        xx_tr = xx - source['MUSE_X_IMAGE']
        yy_tr = yy - source['MUSE_Y_IMAGE']

        if mode == 'kron_ellipse':
            ang = np.deg2rad(source[args.key_theta])

            x_over_a = xx_tr*np.cos(ang) + yy_tr*np.sin(ang)
            x_over_a /= source[args.key_a]
            x_over_a = x_over_a ** 2

            y_over_b = xx_tr*np.sin(ang) - yy_tr*np.cos(ang)
            y_over_b /= source[args.key_b]
            y_over_b = y_over_b ** 2

            mask = (x_over_a + y_over_b) < (1.0/source[args.key_kron])

        elif mode == 'kron_circular':
            kron_circular = source[args.key_kron] * source[args.key_b]
            kron_circular /= source[args.key_a]

            mask = (xx_tr**2 + yy_tr**2) < (kron_circular ** 2)

        elif mode == 'circular_aperture':
            mask = (xx_tr**2 + yy_tr**2) < args.aperture_size / 0.06

        if cube_footprint is not None:
            mask &= cube_footprint

        if args.check_images:
            extracted_data += mask

        obj_spectrum = trasposed_spec[mask].sum(axis=0)

        if args.no_nans is True:
            obj_spec_nan_mask = np.isnan(obj_spectrum)
            obj_spectrum[obj_spec_nan_mask] = 0

        outname = f"spec_{source['NUMBER']:06d}.fits"

        my_time = Time.now()
        my_time.format = 'isot'

        main_header = {
            'CUBE': (basename_with_ext, "Spectral cube used for extraction"),
            'RA': (obj_ra, "Ra of the center of the object"),
            'DEC': (obj_dec, "Dec of the center of the object"),
            'NPIX': (np.sum(mask), 'Number of pixels used for this spectra'),
            'HISTORY': f'Extracted on {str(my_time)}',
        }

        # Copy the spectral part of the WCS into the new FITS
        spec_wcs_header = spectral_wcs.to_header()

        if var_hdu is not None:
            obj_spectrum_var = trasposed_var[mask].sum(axis=0)
            if args.no_nans:
                max_var = np.nanmax(obj_spectrum_var)
                obj_spectrum_var[obj_spec_nan_mask] = 3 * max_var
        else:
            obj_spectrum_var = None

        hdul = getspsinglefits(
            main_header, spec_wcs_header,
            obj_spectrum, spec_hdu.header,
            obj_spectrum_var
        )

        hdul.writeto(
            os.path.join(outdir, outname),
            overwrite=True
        )

    if args.check_images:
        fig, ax = plt.subplots(1, 1, figsize=(10, 10))
        ax.imshow(extracted_data, origin='lower')
        plt.show()


if __name__ == '__main__':
    sys.exit(main())
