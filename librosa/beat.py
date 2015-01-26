#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Beat tracking and tempo estimation"""

import numpy as np
import scipy

from . import cache
from . import core
from . import onset
from . import util

__all__ = ['beat_track', 'estimate_tempo']


@cache
def beat_track(y=None, sr=22050, onset_envelope=None, hop_length=64,
               start_bpm=120.0, tightness=400, trim=True, bpm=None):
    r'''Dynamic programming beat tracker.

    Beats are detected in three stages, following the method of [1]_:
      1. Measure onset strength
      2. Estimate tempo from onset correlation
      3. Pick peaks in onset strength approximately consistent with estimated
         tempo

    .. [1] Ellis, Daniel PW. "Beat tracking by dynamic programming."
           Journal of New Music Research 36.1 (2007): 51-60.
           http://labrosa.ee.columbia.edu/projects/beattrack/


    Parameters
    ----------

    y : np.ndarray [shape=(n,)] or None
        audio time series

    sr : int > 0 [scalar]
        sampling rate of `y`

    onset_envelope : np.ndarray [shape=(n,)] or None
        (optional) pre-computed onset strength envelope.

    hop_length : int > 0 [scalar]
        number of audio samples between successive `onset_envelope` values

    start_bpm  : float > 0 [scalar]
        initial guess for the tempo estimator (in beats per minute)

    tightness  : float [scalar]
        tightness of beat distribution around tempo

    trim       : bool [scalar]
        trim leading/trailing beats with weak onsets

    bpm        : float [scalar]
        (optional) If provided, use `bpm` as the tempo instead of
        estimating it from `onsets`.


    Returns
    -------

    tempo : float [scalar, non-negative]
        estimated global tempo (in beats per minute)

    beats : np.ndarray [shape=(m,)]
        frame numbers of estimated beat events

    .. note::
        If no onset strength could be detected, beat_tracker estimates 0 BPM
        and returns an empty list.


    Raises
    ------

    ValueError
        if neither `y` nor `onset_envelope` are provided


    See Also
    --------
    librosa.onset.onset_strength


    Examples
    --------
    Track beats using time series input

    >>> y, sr = librosa.load(librosa.util.example_audio_file())

    >>> tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    >>> tempo
    130.01179245283018


    Print the first 20 beat frames

    >>> beats[:20]
    array([  23,  177,  341,  501,  658,  815,  976, 1132, 1292, 1447,
           1612, 1773, 1931, 2087, 2248, 2404, 2561, 2724, 2886, 3050])


    Or print them as timestamps

    >>> librosa.frames_to_time(beats[:20], sr=sr, hop_length=64)
    array([ 0.067,  0.514,  0.99 ,  1.454,  1.91 ,  2.366,  2.833,  3.286,
            3.75 ,  4.2  ,  4.679,  5.146,  5.605,  6.058,  6.525,  6.978,
            7.433,  7.906,  8.377,  8.853])


    Track beats using a pre-computed onset envelope

    >>> hop_length = 64
    >>> onset_env = librosa.onset.onset_strength(y, sr=sr,
    ...                                             hop_length=hop_length)
    >>> tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env,
    ...                                        sr=sr,
    ...                                        hop_length=hop_length)
    >>> tempo
    130.011792453
    >>> beats[:20]
    array([  23,  177,  341,  501,  658,  815,  976, 1132, 1292, 1447,
           1612, 1773, 1931, 2087, 2248, 2404, 2561, 2724, 2886, 3050])
    '''

    # First, get the frame->beat strength profile if we don't already have one
    if onset_envelope is None:
        if y is None:
            raise ValueError('Either "y" or "onsets" must be provided')

        onset_envelope = onset.onset_strength(y=y,
                                              sr=sr,
                                              hop_length=hop_length)

    # Do we have any onsets to grab?
    if not onset_envelope.any():
        return (0, np.array([], dtype=int))

    # Estimate BPM if one was not provided
    if bpm is None:
        bpm = estimate_tempo(onset_envelope,
                             sr=sr,
                             hop_length=hop_length,
                             start_bpm=start_bpm)

    # Then, run the tracker
    beats = __beat_tracker(onset_envelope,
                           bpm,
                           float(sr) / hop_length,
                           tightness,
                           trim)

    return (bpm, beats)


@cache
def estimate_tempo(onset_envelope, sr=22050, hop_length=64, start_bpm=120,
                   std_bpm=1.0, ac_size=4.0, duration=90.0, offset=0.0):
    """Estimate the tempo (beats per minute) from an onset envelope


    Parameters
    ----------
    onset_envelope    : np.ndarray [shape=(n,)]
        onset strength envelope

    sr : int > 0 [scalar]
        sampling rate of the time series

    hop_length : int > 0 [scalar]
        hop length of the time series

    start_bpm : float [scalar]
        initial guess of the BPM

    std_bpm : float > 0 [scalar]
        standard deviation of tempo distribution

    ac_size : float > 0 [scalar]
        length (in seconds) of the auto-correlation window

    duration : float > 0 [scalar]
        length of signal (in seconds) to use in estimating tempo

    offset : float > 0 [scalar]
        offset (in seconds) of signal sample to use in estimating tempo


    Returns
    -------
    tempo : float [scalar]
        estimated tempo (beats per minute)


    See Also
    --------
    librosa.onset.onset_strength


    Examples
    --------
    >>> y, sr = librosa.load(librosa.util.example_audio_file())
    >>> hop_length = 64
    >>> onset_env = librosa.onset.onset_strength(y, sr=sr,
    ...                                          hop_length=hop_length)
    >>> librosa.beat.estimate_tempo(onset_env, sr=sr,
    ...                             hop_length=hop_length)
    130.011792453
    """

    fft_res = float(sr) / hop_length

    # Chop onsets to X[(upper_limit - duration):upper_limit]
    # or as much as will fit
    maxcol = int(min(len(onset_envelope)-1,
                     np.round((offset + duration) * fft_res)))

    mincol = int(max(0, maxcol - np.round(duration * fft_res)))

    # Use auto-correlation out of 4 seconds (empirically set??)
    ac_window = min(maxcol, np.round(ac_size * fft_res))

    # Compute the autocorrelation
    x_corr = core.autocorrelate(onset_envelope[mincol:maxcol], ac_window)

    # re-weight the autocorrelation by log-normal prior
    bpms = 60.0 * fft_res / (np.arange(1, ac_window+1))

    # Smooth the autocorrelation by a log-normal distribution
    x_corr = x_corr * np.exp(-0.5 * ((np.log2(bpms / start_bpm)) / std_bpm)**2)

    # Get the local maximum of weighted correlation
    x_peaks = util.localmax(x_corr)

    # Zero out all peaks before the first negative
    x_peaks[:np.argmax(x_corr < 0)] = False

    # Choose the best peak out of .33, .5, 2, 3 * start_period
    candidates = np.argmax(x_peaks * x_corr) * np.asarray([1./3, 0.5, 1, 2, 3])

    candidates = candidates[candidates < ac_window].astype(int)

    best_period = np.argmax(x_corr[candidates])

    if candidates[best_period] > 0:
        return 60.0 * fft_res / candidates[best_period]

    return start_bpm


@cache
def __beat_tracker(onset_envelope, bpm, fft_res, tightness, trim):
    """Internal function that tracks beats in an onset strength envelope.

    Parameters
    ----------
    onset_envelope : np.ndarray [shape=(n,)]
        onset strength envelope

    bpm : float [scalar]
        tempo estimate

    fft_res  : float [scalar]
        resolution of the fft (sr / hop_length)

    tightness: float [scalar]
        how closely do we adhere to bpm?

    trim : bool [scalar]
        trim leading/trailing beats with weak onsets?

    Returns
    -------
    beats : np.ndarray [shape=(n,)]
        frame numbers of beat events
    """

    # convert bpm to a sample period for searching
    period = round(60.0 * fft_res / bpm)

    # localscore is a smoothed version of AGC'd onset envelope
    localscore = __beat_local_score(onset_envelope, period)

    # run the DP
    backlink, cumscore = __beat_track_dp(localscore, period, tightness)

    # get the position of the last beat
    beats = [__last_beat(cumscore)]

    # Reconstruct the beat path from backlinks
    while backlink[beats[-1]] >= 0:
        beats.append(backlink[beats[-1]])

    # Put the beats in ascending order
    # Convert into an array of frame numbers
    beats = np.array(beats[::-1], dtype=int)

    # Discard spurious trailing beats
    beats = __trim_beats(localscore, beats, trim)

    return beats


# -- Helper functions for beat tracking
def __normalize_onsets(onsets):
    '''Maps onset strength function into the range [0, 1]'''

    norm = onsets.std(ddof=1)
    if norm > 0:
        onsets = onsets / norm
    return onsets


def __beat_local_score(onset_envelope, period):
    '''Construct the local score for an onset envlope and given period'''

    window = np.exp(-0.5 * (np.arange(-period, period+1)*32.0/period)**2)
    return scipy.signal.convolve(__normalize_onsets(onset_envelope),
                                 window,
                                 'same')


def __beat_track_dp(localscore, period, tightness):
    """Core dynamic program for beat tracking"""

    backlink = np.zeros_like(localscore, dtype=int)
    cumscore = np.zeros_like(localscore)

    # Search range for previous beat
    window = np.arange(-2 * period, -np.round(period / 2) + 1, dtype=int)

    # Make a score window, which begins biased toward start_bpm and skewed
    txwt = -tightness * (np.log(-window / period) ** 2)

    # Are we on the first beat?
    first_beat = True
    for i, score_i in enumerate(localscore):

        # Are we reaching back before time 0?
        z_pad = np.maximum(0, min(- window[0], len(window)))

        # Search over all possible predecessors
        candidates = txwt.copy()
        candidates[z_pad:] = candidates[z_pad:] + cumscore[window[z_pad:]]

        # Find the best preceding beat
        beat_location = np.argmax(candidates)

        # Add the local score
        cumscore[i] = score_i + candidates[beat_location]

        # Special case the first onset.  Stop if the localscore is small
        if first_beat and score_i < 0.01 * localscore.max():
            backlink[i] = -1
        else:
            backlink[i] = window[beat_location]
            first_beat = False

        # Update the time range
        window = window + 1

    return backlink, cumscore


def __last_beat(cumscore):
    """Get the last beat from the cumulative score array"""

    maxes = util.localmax(cumscore)
    med_score = np.median(cumscore[np.argwhere(maxes)])

    # The last of these is the last beat (since score generally increases)
    return np.argwhere((cumscore * maxes * 2 > med_score)).max()


def __trim_beats(localscore, beats, trim):
    """Final post-processing: throw out spurious leading/trailing beats"""

    smooth_boe = scipy.signal.convolve(localscore[beats],
                                       scipy.signal.hann(5),
                                       'same')

    if trim:
        threshold = 0.5 * ((smooth_boe**2).mean()**0.5)
    else:
        threshold = 0.0

    valid = np.argwhere(smooth_boe > threshold)

    return beats[valid.min():valid.max()]
