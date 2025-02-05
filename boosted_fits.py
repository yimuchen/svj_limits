"""
Building blocks to create the boosted SVJ analysis datacards
"""

import uuid, sys, time, argparse
from contextlib import contextmanager
from array import array
from math import sqrt
import numpy as np
import itertools, re, logging, os, os.path as osp, copy, subprocess, json
from collections import OrderedDict
from time import strftime

PY3 = sys.version_info.major == 3

def encode(s):
    """For python 2/3 compatibility"""
    return s.encode() if PY3 else s


import ctypes
import ROOT # type:ignore
ROOT.TH1.SetDefaultSumw2()
ROOT.TH1.AddDirectory(False)
ROOT.gROOT.SetStyle('Plain')
ROOT.gROOT.SetBatch()
ROOT.gStyle.SetPadBorderMode(0)
ROOT.gStyle.SetPadColor(0)
ROOT.gSystem.Load("libHiggsAnalysisCombinedLimit.so")


DEFAULT_LOGGING_LEVEL = logging.INFO
def setup_logger(name='boosted'):
    if name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(name)
        logger.info('Logger %s is already defined', name)
    else:
        fmt = logging.Formatter(
            fmt = '\033[33m%(levelname)s:%(asctime)s:%(module)s:%(lineno)s\033[0m %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
            )
        handler = logging.StreamHandler()
        handler.setFormatter(fmt)
        logger = logging.getLogger(name)
        logger.setLevel(DEFAULT_LOGGING_LEVEL)
        logger.addHandler(handler)
    return logger
logger = setup_logger()
subprocess_logger = setup_logger('subp')
subprocess_logger.handlers[0].formatter._fmt = '\033[34m[%(asctime)s]\033[0m %(message)s'


def debug(flag=True):
    """Sets the logger level to debug (for True) or warning (for False)"""
    logger.setLevel(logging.DEBUG if flag else DEFAULT_LOGGING_LEVEL)


DRYMODE = False
def drymode(flag=True):
    global DRYMODE
    DRYMODE = bool(flag)


def pull_arg(*args, **kwargs):
    """
    Pulls specific arguments out of sys.argv.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(*args, **kwargs)
    args, other_args = parser.parse_known_args()
    sys.argv = [sys.argv[0]] + other_args
    return args


def read_arg(*args, **kwargs):
    """
    Reads specific arguments from sys.argv but does not modify sys.argv
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(*args, **kwargs)
    args, _ = parser.parse_known_args()
    return args

def get_xs(mz):
    signal_xsecs = {
        200 : 9.143,
        250 : 6.910,
        300 : 5.279,
        350 : 4.077,
        400 : 3.073,
        450 : 2.448,
        500 : 1.924,
        550 : 1.578,
    }
    return signal_xsecs[mz]

@contextmanager
def set_args(args):
    _old_sys_args = sys.argv
    try:
        sys.argv = args
        yield args
    finally:
        sys.argv = _old_sys_args

@contextmanager
def reset_sys_argv():
    """
    Saves a copy of sys.argv, and resets it upon closing this context
    """
    saved_sys_args = sys.argv[:]
    try:
        yield None
    finally:
        sys.argv = saved_sys_args

@contextmanager
def timeit(msg):
    try:
        logger.info(msg)
        sys.stdout.flush()
        t0 = time.time()
        yield None
    finally:
        logger.info('  Done, took %s secs', time.time() - t0)

class Scripter:
    def __init__(self):
        self.scripts = {}

    def __call__(self, fn):
        self.scripts[fn.__name__] = fn
        return fn

    def run(self):
        script = pull_arg('script', choices=list(self.scripts.keys())).script
        logger.info('Running %s', script)
        self.scripts[script]()


def mpl_fontsizes(small=14, medium=18, large=24):
    import matplotlib.pyplot as plt # type:ignore
    plt.rc('font', size=small)          # controls default text sizes
    plt.rc('axes', titlesize=small)     # fontsize of the axes title
    plt.rc('axes', labelsize=medium)    # fontsize of the x and y labels
    plt.rc('xtick', labelsize=small)    # fontsize of the tick labels
    plt.rc('ytick', labelsize=small)    # fontsize of the tick labels
    plt.rc('legend', fontsize=small)    # legend fontsize
    plt.rc('figure', titlesize=large)   # fontsize of the figure title


@contextmanager
def quick_ax(figsize=(12,12), outfile='temp.png'):
    import matplotlib.pyplot as plt #type: ignore
    try:
        fig = plt.figure(figsize=figsize)
        ax = fig.gca()
        yield ax
    finally:
        plt.savefig(outfile, bbox_inches='tight')
        os.system('imgcat ' + outfile)


def uid():
    return str(uuid.uuid4())


class AttrDict(dict):
    """
    Like a dict, but with access via attributes
    """
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


# _______________________________________________________________________
# JSON Input interface

def hist_to_th1(name, hist):
    """
    Takes a dict-like histogram (keys: binning, vals, errs) and
    returns a ROOT.TH1F
    """
    logger.warning('Deprecated: use Histogram.th1(name) instead')
    return hist.th1(name)


def hist_cut_left(hist, i_bin_min):
    """
    Returns a new hist with i_bin_min as bin 0, and any preceeding bin
    thrown out
    """
    if i_bin_min == 0: return hist
    histcopy = dict(hist)
    for key in ['binning', 'vals', 'errs']:
        histcopy[key] = hist[key][i_bin_min:]
    return AttrDict(histcopy)


class Histogram:
    def __init__(self, d):
        self.vals = np.array(d['vals'])
        self.errs = np.array(d['errs'])
        self.binning = np.array(d['binning'])
        self.metadata = d['metadata']

    def copy(self):
        """Returns a copy"""
        return copy.deepcopy(self)

    def th1(self, name):
        """
        Converts histogram to a ROOT.TH1F
        """
        n_bins = len(self.binning)-1
        th1 = ROOT.TH1F(name, name, n_bins, self.binning)
        ROOT.SetOwnership(th1, False)
        assert len(self.vals) == n_bins
        assert len(self.errs) == n_bins
        for i in range(n_bins):
            th1.SetBinContent(i+1, self.vals[i])
            th1.SetBinError(i+1, self.errs[i])
        return th1

    @property
    def nbins(self):
        return len(self.binning)-1

    def __repr__(self):
        d = np.column_stack((self.vals, self.errs))
        return (
            f'<H n={self.nbins} int={self.vals.sum():.3f}'
            f' binning={self.binning[0]:.1f}-{self.binning[-1]:.1f}'
            f' vals/errs=\n{d}'
            '>'
            )

    def __eq__(self,other):
        return (self.vals==other.vals).all() and (self.errs==other.errs).all() and (self.binning==other.binning).all()

    def cut(self, xmin=-np.inf, xmax=np.inf):
        """
        Throws away all bins with left boundary < xmin or right boundary > xmax.
        Mostly useful for plotting purposes.
        Returns a copy.
        """
        # safety checks
        if xmin>xmax:
            raise ValueError("xmin ({}) greater than xmax ({})".format(xmin,xmax))

        h = self.copy()
        imin = np.argmin(self.binning < xmin) if xmin > self.binning[0] else 0
        imax = np.argmax(self.binning > xmax) if xmax < self.binning[-1] else self.nbins+1
        h.binning = h.binning[imin:imax]
        h.vals = h.vals[imin:imax-1]
        h.errs = h.errs[imin:imax-1]
        return h

def build_histograms_in_dict_tree(d, parent=None, key=None):
    """
    Traverses a dict-of-dicts, and converts everything that looks like a 
    histogram to a Histogram object.
    """
    n_histograms = 0
    if not isinstance(d, dict): return 0
    if 'binning' in d: # It's a histogram / deprecate to 'type' : 'histogram' later
        parent[key] = Histogram(d)
        return 1
    else:
        for key, value in d.items():
            n_histograms += build_histograms_in_dict_tree(value, parent=d, key=key)
    return n_histograms


def iter_histograms(d):
    """
    Traverses a dict-of-dicts, and yields all the Histogram instances.
    """
    if isinstance(d, Histogram):
        yield d
    elif isinstance(d, dict):
        for v in d.values():
            for _ in iter_histograms(v):
                yield _

def cut_histograms(d,mt_min,mt_max):
    """
    Traverses a dict-of-dicts, and cuts all the Histogram instances
    """
    if isinstance(d, Histogram):
        return d.cut(mt_min,mt_max)
    elif isinstance(d, dict):
        for k,v in d.items():
            d[k] = cut_histograms(v,mt_min,mt_max)
        return d

def ls_inputdata(d, depth=0, key='<root>'):
    """
    Prints a dict of dicts recursively
    """
    if isinstance(d, Histogram):
        print('  '*depth + key + ' (histogram)')
    elif isinstance(d, dict):
        print('  '*depth + key)
        for k, v in sorted(d.items()):
            ls_inputdata(v, depth+1, k)


class Decoder(json.JSONDecoder):
    """
    Standard JSON decoder, but support for the Histogram class
    """
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, d):
        try:
            is_histogram = d['type'] == 'Histogram'
        except (AttributeError, KeyError):
            is_histogram = False
        if is_histogram:
            return Histogram(d)
        return d

# get set of json files from command line (used for datacard creation)
def get_jsons():
    result = dict(
        sigfile = pull_arg('--sig', type=str, required=True).sig,
        bkgfile = pull_arg('--bkg', type=str, required=True).bkg,
        datafile = pull_arg('--data', type=str, default=None).data,
    )
    return result

# all_pdfs can have more values than in this list
# this list controls what actually runs
def known_pdfs():
    pdf_list = ["main", "alt", "ua2"]
    return pdf_list

def make_pdf(name, mt, bkg_th1):
    return pdfs_factory(name, mt, bkg_th1, name=f'bsvj_bkgfit{name}')

class PdfInfo(object):
    # format of info:
    # {i: {"expr": str, "pars": {j: (a,b), k: (c,d)}]}, ...}
    # if par ranges not provided, default is (-100,100)
    def __init__(self, name, info):
        self.name = name
        self.info = info
        self.n_max = max(info.keys())
        self.n_min = min(info.keys())
    def check_n(self, n):
        if n<self.n_min or n>self.n_max:
            raise Exception(f'Unavailable npars for {self.name} (allowed: {n_min} to {n_max})')
    def expression(self, n, mt_scale=1000.):
        self.check_n(n)
        return self.info[n]["expr"].format(mt_scale)
    def parameters(self, n, prefix=None):
        self.check_n(n)
        if prefix is None: prefix = uid()
        par_ranges = self.info[n].get("pars",{})
        par_ranges = {i : par_ranges.get(i,(-100.,100.)) for i in range(1,n+1)}
        parameters = [ROOT.RooRealVar(f'{prefix}_p{i}', f'p{i}', 1., par_ranges[i][0], par_ranges[i][1]) for i in range(1,n+1)]
        object_keeper.add_multiple(parameters)
        return parameters
    def min_range(self, n, i):
        self.check_n(n)
        min_ranges = self.info[n].get("mins",{})
        min_range = min_ranges.get(i, (None,None))
        return min_range
    def max_range(self, n, i):
        self.check_n(n)
        max_ranges = self.info[n].get("maxs",{})
        max_range = max_ranges.get(i, (None,None))
        return max_range

all_pdfs = {
    # Function from Theorists, combo testing, sequence E, 1, 11, 12, 22
    # model NM has N params on 1-x and M params on x. exponents are (p_i + p_{i+1} * log(x))
    "main": PdfInfo("main", {
        2: {
            "expr": 'pow(1 - @0/{0}, @1) * pow(@0/{0}, -(@2))',
            "pars": {1: (-30., 30.), 2: (-10., 10.)},
        },
        3: {
            "expr": 'pow(1 - @0/{0}, @1) * pow(@0/{0}, -(@2+@3*log(@0/{0})))',
            "pars": {1: (-45., 45.), 2: (-10., 10.), 3: (-15, 15)},
        },
        4: {
            "expr": 'pow(1 - @0/{0}, @1) * pow(@0/{0}, -(@2+@3*log(@0/{0})+@4*pow(log(@0/{0}),2)))',
            # Alternatives to 22:
            # 13: pow(1 - @0/{0}, @1+@2*log(@0/{0})) * pow(@0/{0}, -(@3+@4*log(@0/{0})))
            "pars": {1: (-95., 95.), 2: (-25., 20.), 3: (-2., 2.), 4: (-2., 2.)},
        },
        5: {
            "expr": 'pow(1 - @0/{0}, @1+@2*log(@0/{0})+@3*pow(log(@0/{0}),2)) * pow(@0/{0}, -(@4+@5*log(@0/{0})))',
            # Alternatives to 32:
            # 14: pow(1 - @0/{0}, @1) * pow(@0/{0}, -(@2+@3*log(@0/{0})+@4*pow(log(@0/{0}),2)+@5*pow(log(@0/{0}),3)))
            # 41: pow(1 - @0/{0}, @1+@2*log(@0/{0})+@3*pow(log(@0/{0}),2)+@4*pow(log(@0/{0}),3)) * pow(@0/{0}, -@5)
            "pars": {1: (-15., 15.), 2: (-95., 95.), 3: (-25., 25.), 4: (-5., 5.), 5: (-1.5, 1.5)},
        }
    }),
    "alt": PdfInfo("alt", {
        2: {
            "expr": 'exp(@1*(@0/{0})) * pow(@0/{0},@2)',
            "pars": {1: (-50., 50.), 2: (-10., 10.)},
        },
        3: {
            "expr": 'exp(@1*(@0/{0})) * pow(@0/{0},@2*(1+@3*log(@0/{0})))',
        },
        4: {
            "expr": 'exp(@1*(@0/{0})) * pow(@0/{0},@2*(1+@3*log(@0/{0})*(1+@4*log(@0/{0}))))',
            "pars": {1: (-150., 150.), 2: (-100., 100.), 3: (-10., 10.), 4: (-10., 10.)},
        },
    }),
    "ua2": PdfInfo("ua2", {
        2: {
            "expr": 'pow(@0/{0}, @1) * exp(@0/{0}*(@2))',
        },
        3: {
            "expr": 'pow(@0/{0}, @1) * exp(@0/{0}*(@2+ @3*@0/{0}))',
        },
        4: {
            "expr": 'pow(@0/{0}, @1) * exp(@0/{0}*(@2+ @3*@0/{0} + @4*pow(@0/{0},2)))',
        },
        5: {
            "expr": 'pow(@0/{0}, @1) * exp(@0/{0}*(@2+ @3*@0/{0} + @4*pow(@0/{0},2) + @5*pow(@0/{0},3)))',
        },
    }),
    "ua2mod": PdfInfo("ua2mod", {
        1: {
            "expr": 'exp(@1*@0/{0})',
        },
        2: {
            "expr": 'exp(@1*@0/{0} + @2*pow(@0/{0},2))',
        },
        3: {
            "expr": 'exp(@1*@0/{0} + @2*pow(@0/{0},2) + @3*pow(@0/{0},3))',
        },
        4: {
            "expr": 'exp(@1*@0/{0} + @2*pow(@0/{0},2) + @3*pow(@0/{0},3) + @4*pow(@0/{0},4))',
        },
        5: {
            "expr": 'exp(@1*@0/{0} + @2*pow(@0/{0},2) + @3*pow(@0/{0},3) + @4*pow(@0/{0},4) + @5*pow(@0/{0},5))',
        },
    }),
    "modexp": PdfInfo("modexp", {
        2: {
            "expr": 'exp(@1*pow(@0/{0}, @2))',
            "pars": {1: (-20, 0), 2: (0, 10)},
        },
        3: {
            "expr": 'exp(@1*pow(@0/{0}, @2)+@1*pow(1-@0/{0}, @3))',
            "pars": {1: (-20, 0), 2: (0, 10), 3: (-10, 0)},
        },
        4: {
            "expr": 'exp(@1*pow(@0/{0}, @2)+@4*pow(1-@0/{0}, @3))',
            "pars": {1: (-20, 0), 2: (0, 10), 3: (-10, 0), 4: (-20, 0)},
        },
    }),
    "polpow": PdfInfo("polpow", {
        2: {
            "expr": 'pow(1 + @1*@0/{0},-@2)',
            "pars": {1: (0, 50), 2: (-50, 0)},
        },
        3: {
            "expr": 'pow(1 + @1*@0/{0} + @2*pow(@0/{0},2),-@3)',
            "pars": {1: (0, 50), 2: (0, 50), 3: (-50, 0)},
        },
        4: {
            "expr": 'pow(1 + @1*@0/{0} + @2*pow(@0/{0},2) + @3*pow(@0/{0},3),-@4)',
            "pars": {1: (0, 50), 2: (0, 50), 3: (0, 50), 4: (-50, 0)},
        },
        5: {
            "expr": 'pow(1 + @1*@0/{0} + @2*pow(@0/{0},2) + @3*pow(@0/{0},3) + @4*pow(@0/{0},4),-@5)',
            "pars": {1: (0, 50), 2: (0, 50), 3: (0, 50), 4: (0, 50), 5: (-50, 0)},
        },
    }),
}

def PoissonErrorUp(N):
    alpha = 1 - 0.6827 #1 sigma interval
    U = ROOT.Math.gamma_quantile_c(alpha/2,N+1,1.)
    return U-N

class InputData(object):
    """
    9 Feb 2024: Reworked .json input format.
    Now only one signal model per .json file, so one InputDataV2 instance represents
    one signal model parameter variation.
    That way, datacard generation can be made class methods.
    """
    def __init__(self, mt_min=180., mt_max=650., **kwargs):
        self.asimov = kwargs.pop('asimov',False)
        for file in ['sigfile','bkgfile','datafile']:
            setattr(self, file, kwargs.pop(file))
            obj = file.replace('file','')
            if getattr(self, file) is None:
                setattr(self, obj, None)
            else:
                if file=='bkgfile' and self.asimov:
                    f = ROOT.TFile.Open(getattr(self,file))
                    atoy = f.Get("toys/toy_asimov")
                    setattr(self, obj, atoy)
                    f.Close()
                    continue
                with open(getattr(self,file), 'r') as f:
                    d = json.load(f, cls=Decoder)
                    # cut mt range
                    d = cut_histograms(d,mt_min,mt_max)
                    # check for unneeded signal systematics
                    if obj=='sig':
                        c = d['central']
                        # drop mcstat histograms that have no difference from central
                        d = {k:v for k,v in d.items() if not k.startswith('mcstat') or v!=c}
                    setattr(self, obj, d)

        self.mt = self.sig['central'].binning
        self.metadata = self.sig['central'].metadata

        # further initializations
        self.mtvar = get_mt(self.mt[0], self.mt[-1], self.n_bins, name='mt')

        if self.asimov:
            self.data_datahist = self.bkg.binnedClone("data_obs")
            self.bkg_th1 = self.data_datahist.createHistogram("mt")
            # RooFit sets err = w when creating weighted histograms
            for i in range(self.bkg_th1.GetNbinsX()):
                self.bkg_th1.SetBinError(i+1,PoissonErrorUp(self.bkg_th1.GetBinContent(i+1)))
        else:
            self.bkg_th1 = self.bkg['bkg'].th1('bkg')
            if self.data is not None:
                data_th1 = self.data['data'].th1('data')
            else:
                data_th1 = self.bkg_th1
            self.data_datahist = ROOT.RooDataHist("data_obs", "Data", ROOT.RooArgList(self.mtvar), data_th1, 1.)

    def copy(self):
        return copy.deepcopy(self)

    @property
    def mt_array(self):
        return np.array(self.mt)

    @property
    def n_bins(self):
        return len(self.mt)-1

    def gen_datacard(self, use_cache=True, fit_cache_lock=None, nosyst=False, gof_type='rss', winners=None, brute=False):
        mz = int(self.metadata['mz'])
        rinv = float(self.metadata['rinv'])
        mdark = int(self.metadata['mdark'])

        pdfs_dict = {pdf : make_pdf(pdf, self.mtvar, self.bkg_th1) for pdf in known_pdfs()}

        cache = None
        if use_cache:
            from fit_cache import FitCache
            cache = FitCache(lock=fit_cache_lock)

        winner_indices = winners if winners else {}
        winner_pdfs = []
        for pdf_type in pdfs_dict:
            pdfs = pdfs_dict[pdf_type]
            ress = [ fit(pdf, cache=cache, brute=brute) for pdf in pdfs ]
            i_winner = do_fisher_test(self.mtvar, self.data_datahist, pdfs, gof_type=gof_type)
            # take i_winner if pdf not in manually specified dictionary of winner indices
            i_winner_final = winner_indices.get(pdf_type, i_winner)
            logger.info(f'gen_datacard: chose n_pars={pdfs[i_winner_final].n_pars} for {pdf_type}')
            winner_pdfs.append(pdfs[i_winner_final])

        systs = [
            ['lumi', 'lnN', 1.016, '-'],
            ['trigger_cr', 'lnN', 1.02, '-'],
            ['trigger_sim', 'lnN', 1.021, '-'],
            ['mZprime','extArg', mz],
            ['mDark',  'extArg', mdark],
            ['rinv',   'extArg', rinv],
            ['xsec',   'extArg', get_xs(mz)],
            ]

        sig_name = 'mz{:.0f}_rinv{:.1f}_mdark{:.0f}'.format(mz, rinv, mdark)
        sig_th1 = self.sig['central'].th1(sig_name)
        sig_datahist = ROOT.RooDataHist(sig_name, sig_name, ROOT.RooArgList(self.mtvar), sig_th1, 1.)

        syst_th1s = []
        used_systs = set()
        for key, hist in self.sig.items():
            if '_up' in key:
                direction = 'Up'
            elif '_down' in key:
                direction = 'Down'
            else:
                continue # Not a systematic
            syst_name = key.replace('_up','').replace('_down','')
            # Don't add the line twice
            if syst_name not in used_systs:
                systs.append([syst_name, 'shape', 1, '-'])
                used_systs.add(syst_name)
            syst_th1s.append(hist.th1(f'{sig_name}_{syst_name}{direction}'))

        nosystname = ""
        if nosyst:
            systs = [s for s in systs if s[1]=='extArg']
            nosystname = "_nosyst"
        if self.asimov:
            nosystname = nosystname + "_asimov"

        outfile = strftime(f'dc_%Y%m%d_{self.metadata["selection"]}{nosystname}/dc_{osp.basename(self.sigfile).replace(".json","")}{nosystname}.txt')
        compile_datacard_macro(
            winner_pdfs, self.data_datahist, sig_datahist,
            outfile,
            systs=systs,
            syst_th1s=syst_th1s,
        )



# _______________________________________________________________________
# Model building code: Bkg fits, fisher testing, etc.


def dump_fits_to_file(filename, results):
    logger.info('Dumping fit results to ' + filename)
    dirname = osp.dirname(osp.abspath(filename))
    if not osp.isdir(dirname): os.makedirs(dirname)
    with open_root(filename, 'RECREATE') as tf:
        for result in results: result.Write()


def dump_ws_to_file(filename, ws):
    logger.info('Dumping ws {} to {}'.format(ws.GetName(), filename))
    dirname = osp.dirname(osp.abspath(filename))
    if not osp.isdir(dirname): os.makedirs(dirname)
    wstatus = ws.writeToFile(filename, True)
    return wstatus


def eval_expression(expression, pars):
    """
    Evaluates a ROOT TFormula expression in python.
    Only a limited amount of keywords are implemented (pow, log, sqrt, exp).
    """
    # Load keywords in local scope
    from numpy import log, sqrt, exp
    def pow(base, exponent):
        return base ** exponent
    # Python variables can't start with '@'; replace with some keyword
    expression = expression.replace('@', 'PARAMETER')
    # Plug parameters in local scope
    par_dict = {'PARAMETER'+str(i) : p for i, p in enumerate(pars)}
    locals().update(par_dict)
    # logger.warning('Evaluating expr:\n%s\nwith parameters:\n%s', expression, par_dict)
    try:
        return eval(expression)
    except NameError:
        logger.error(
            'Missing variables for expression:\n{0}\nAvailable parameters: {1}'
            .format(expression, list(par_dict.keys()))
            )
        raise


def eval_pdf_python(pdf, parameters, mt_array=None):
    if mt_array is None:
        mt = pdf.parameters[0]
        binning = mt.getBinning()
        mt_array = np.array([ binning.binCenter(i) for i in range(binning.numBins()) ])
    parameters = list(copy.copy(parameters))
    parameters.insert(0, mt_array)
    return eval_expression(pdf.expression, parameters)


def count_parameters(expr):
    """Returns the number of parameters in an expression (i.e. highest @\d"""
    return max(map(int, re.findall(r'@(\d+)', expr))) + 1


def add_normalization(expr):
    """
    Takes an expression string, and basically adds "@NORM*(...)" around it.
    """
    return '@{0}*('.format(count_parameters(expr)) + expr + ')'


def build_rss(expr, th1):
    """
    Builds a residual-sum-of-squares function between a pdf (expression)
    and a histogram.
    """
    # binning, counts = th1_binning_and_values(h)
    hist = th1_to_hist(th1)
    # counts /= counts.sum() # normalize to 1
    bin_centers = [.5*(l+r) for l, r in zip(hist.binning[:-1], hist.binning[1:])]
    mtarray = np.array(bin_centers)
    def rss(parameters):
        # Insert mT array as first parameter
        parameters = list(copy.copy(parameters))
        parameters.insert(0, mtarray)
        y_pdf = eval_expression(expr, parameters)
        # Normalize pdf to counts too before evaluating, so as the compare only shape
        y_pdf = (y_pdf/y_pdf.sum()) * hist.vals.sum()
        return np.sqrt(np.sum((hist.vals-y_pdf)**2))
    return rss


def build_chi2(expr, h):
    """
    Builds a chi2 function between a pdf (expression) and a histogram.
    """
    hist = th1_to_hist(h)
    # Use the bin centers as the mT array
    mt_array = np.array(.5*(hist.binning[:-1]+hist.binning[1:]))
    def chi2(parameters):
        # Construct the parameters of the expression:
        # [ @0 (mt), @1, ... @N (pdf parameters) ]
        parameters = list(copy.copy(parameters))
        parameters.insert(0, mt_array)
        y_pdf = eval_expression(expr, parameters)
        # Normalize pdf to counts too before evaluating, so as the compare only shape
        y_pdf = (y_pdf/y_pdf.sum()) * hist.vals.sum()
        return np.sum((hist.vals-y_pdf)**2 / y_pdf)
    return chi2


def make_fit_hash(expression, th1, init_vals=None, tag=None, **minimize_kwargs):
    """
    Constructs a hash from all the input data of a fit:
    - The expression (as a string)
    - The histogram (binning and values, not errors)
    - The initial values set for the fit
    - The scipy minimizer arguments
    - Any user provided tag
    """
    import hashlib
    m = hashlib.sha256()
    def add_floats_to_hash(floats):
        for number in floats:
            s = '{:.5f}'.format(number)
            m.update(encode(s))
    m.update(encode(expression))
    hist = th1_to_hist(th1)
    add_floats_to_hash(hist.binning)
    add_floats_to_hash(hist.vals)
    if init_vals is not None: add_floats_to_hash(init_vals)
    if 'tol' in minimize_kwargs: m.update(encode('{:.3f}'.format(minimize_kwargs['tol'])))
    if 'method' in minimize_kwargs: m.update(encode(minimize_kwargs['method']))
    if tag: m.update(encode(tag))
    return m.hexdigest()


def fit_roofit(pdf, data_hist=None, init_vals=None, init_ranges=None):
    """
    Main bkg fit entry point for fitting pdf to bkg th1 with RooFit
    """
    # Preparation
    if data_hist is None: data_hist = pdf.th1
    if isinstance(data_hist, ROOT.TH1): data_hist = th1_to_datahist(data_hist, pdf.mt)

    logger.info('Fitting pdf {0} to data_hist {1} with RooFit'.format(pdf, data_hist))

    if init_vals is not None:
        if len(init_vals) != len(pdf.parameters):
            raise Exception('Expected {} values; got {}'.format(len(pdf.parameters)-1, len(init_vals)))
        for ipar, (par, value) in enumerate(zip(pdf.parameters, init_vals)):
            left, right = par.getMin(), par.getMax()

            # First check if the init_val is *outside* of the current range:
            if value < left:
                new_left = value -10.*abs(value)
                max_range = all_pdfs[pdf.pdf_type].max_range(pdf.n_pars, ipar+1)
                logger.info(f'Checking max range for {pdf.pdf_type} {pdf.n_pars} {ipar} {par.GetName()} -> {max_range}')
                if max_range[0] is not None: new_left = max(new_left, max_range[0])
                logger.info(
                    f'Increasing range for {par.GetName()} on the left:'
                    f'({left:.2f}, {right:.2f}) -> ({new_left:.2f}, {right:.2f})'
                    )
                par.setMin(new_left)
            elif value > right:
                new_right = value + 10.*abs(value)
                max_range = all_pdfs[pdf.pdf_type].max_range(pdf.n_pars, ipar+1)
                logger.info(f'Checking max range for {pdf.pdf_type} {pdf.n_pars} {ipar} {par.GetName()} -> {max_range}')
                if max_range[1] is not None: new_right = min(new_right, max_range[1])
                logger.info(
                    f'Increasing range for {par.GetName()} on the right:'
                    f'({left:.2f}, {right:.2f}) -> ({left:.2f}, {new_right:.2f})'
                    )
                par.setMax(new_right)

            # Now check if any of the ranges are needlessly large
            eps = 1e-10
            if abs(value) / (min(abs(left), abs(right))+eps) < 0.1:
                new_left = -10.*abs(value)
                new_right = 10.*abs(value)
                min_range = all_pdfs[pdf.pdf_type].min_range(pdf.n_pars, ipar+1)
                logger.info(f'Checking min range for {pdf.pdf_type} {pdf.n_pars} {ipar} {par.GetName()} -> {min_range}')
                if min_range[0] is not None: new_left = min(new_left, min_range[0])
                if min_range[1] is not None: new_right = max(new_right, min_range[1])
                logger.info(
                    f'Decreasing range for {par.GetName()} on both sides:'
                    f'({left:.2f}, {right:.2f}) -> ({new_left:.2f}, {new_right:.2f})'
                    )
                par.setMin(new_left)
                par.setMax(new_right)

            # Once all the ranges are updated, set the actual initial value
            par.setVal(value)
            logger.info(
                'Setting {0} ({1}) value to {2}, range is {3} to {4}'
                .format(par.GetName(), par.GetTitle(), value, par.getMin(), par.getMax())
                )

    if init_ranges is not None:
        if len(init_ranges) != len(pdf.parameters):
            raise Exception('Expected {} values; got {}'.format(len(pdf.parameters), len(init_ranges)))
        for par, (left, right) in zip(pdf.parameters, init_ranges):
            par.setRange(left, right)
            logger.info(
                'Setting {0} ({1}) range to {2} to {3}'
                .format(par.GetName(), par.GetTitle(), par.getMin(), par.getMax())
                )

    try:
        res = pdf.pdf.fitTo(
            data_hist,
            ROOT.RooFit.Extended(False),
            ROOT.RooFit.Save(1),
            ROOT.RooFit.SumW2Error(True),
            ROOT.RooFit.Strategy(2),
            ROOT.RooFit.Minimizer("Minuit2"),
            ROOT.RooFit.PrintLevel(2 if logger.level <= logging.DEBUG else -1),
            ROOT.RooFit.Range('Full'),
            ROOT.RooFit.PrintEvalErrors(-1)
            )
    except:
        logger.error('Problem fitting pdf {}'.format(pdf.pdf.GetName()))
        raise

    if logger.level <= logging.INFO: res.Print()
    return res


def single_fit_scipy(expression, histogram, init_vals=None, cache=None, **minimize_args):
    """
    Fits a RooFit-style expression (as a string) to a TH1 histogram.

    If cache is a FitCache object, the fit result is stored in the cache.
    """
    fit_hash = make_fit_hash(expression, histogram, init_vals=init_vals, **minimize_args)
    if cache and cache.get(fit_hash):
        logger.info('Returning cached fit')
        return cache.get(fit_hash) # Second call is cheap
    # Do the fit
    n_fit_pars = count_parameters(expression) - 1 # -1 because par 0 is mT
    logger.info('Fitting {0} with {1} parameters'.format(expression, n_fit_pars))
    from scipy.optimize import minimize # type:ignore
    chi2 = build_chi2(expression, histogram)
    if init_vals is None: init_vals = np.ones(n_fit_pars)
    res = minimize(chi2, init_vals, **minimize_args)
    # Save some extra information in the result
    res.x_init = np.array(init_vals)
    res.expression = expression
    res.hash = fit_hash
    # Set approximate uncertainties; see https://stackoverflow.com/a/53489234
    # Assume ftol ~ function value
    # try:
    #     res.dx = np.sqrt(res.fun * np.diagonal(res.hess_inv))
    # except:
    #     logger.error('Failed to set uncertainties; using found function values as proxies')
    #     res.dx = res.x.copy()
    if cache:
        logger.info('Writing fit to cache')
        cache.write(fit_hash, res)
    return res


def fit_scipy_robust(expression, histogram, cache='auto', brute=False):
    """
    Main entry point for fitting an expression to a histogram with Scipy
    """
    logger.info('Robust scipy fit of expression %s to %s', expression, histogram)
    fit_hash = make_fit_hash(expression, histogram, tag='robust')

    if cache == 'auto':
        from fit_cache import FitCache # type: ignore
        cache = FitCache()

    if cache and cache.get(fit_hash):
        res = cache.get(fit_hash) # Second call is cheap
        logger.info('Returning cached fit:\n%s', res)
        return res

    # Attempt 1: Fit with loose tolerance BFGS, then strict tolerance Nelder-Mead
    res = single_fit_scipy(
        expression, histogram,
        tol=1e-3, method='BFGS',
        cache=cache
        )
    # Refit with output from first fit
    options_nm = {'maxfev':10000}
    res = single_fit_scipy(
        expression, histogram,
        init_vals=res.x,
        tol=1e-6, method='Nelder-Mead',
        cache=cache,
        options = options_nm
        )

    if res.success and not brute:
        # Fit successful, save in the cache and return
        if cache: cache.write(fit_hash, res)
        logger.info('Converged with simple fitting strategy, result:\n%s', res)
        return res

    # The simple fitting scheme failed; Brute force with many different
    # initial values
    npars = count_parameters(expression)-1 # The mT parameter is not a fit parameter
    init_val_variations = [-1., 1.] # All the possible init values a single fit parameter can have
    init_vals = np.array(list(itertools.product(*[init_val_variations for i in range(npars)])))
    logger.info(
        'Fit did not converge with single try; brute forcing it with '
        '%s different variations of initial values with both BFGS and Nelder-Mead.',
        len(init_vals)
        )
    results = []
    for method in ['BFGS', 'Nelder-Mead']:
        for init_val_variation in init_vals:
            options_single = options_nm if method=='Nelder-Mead' else {}
            result = single_fit_scipy(
                expression, histogram,
                init_vals=init_val_variation,
                tol=1e-3, method=method,
                options = options_single
                )
            # Check if fit fn val is not NaN or +/- inf
            if not(np.isnan(result.fun) or np.isposinf(result.fun) or np.isneginf(result.fun)):
                results.append(result)
    if len(results) == 0: raise Exception('Not a single fit of the brute force converged!')
    i_min = np.argmin([r.fun for r in results])
    res = results[i_min]
    logger.info('Best scipy fit from brute force:\n%s', res)
    if cache: cache.write(fit_hash, res)
    return res


def fit(pdf, th1=None, cache='auto', brute=False):
    """
    Main bkg fit entry point for
    - first fitting pdf expression to bkg th1 with scipy
    - then using those initial values in RooFit
    """
    if th1 is None: th1 = getattr(pdf, 'th1', None)
    res_scipy = fit_scipy_robust(pdf.expression, th1, cache=cache, brute=brute)
    res_roofit_wscipy = fit_roofit(pdf, th1, init_vals=res_scipy.x)
    return res_roofit_wscipy



def get_mt(mt_min, mt_max, n_bins, name=None):
    """
    Sensible defaults for the mt axis
    """
    if name is None: name = uid()
    mt = ROOT.RooRealVar(name, 'm_{T}', mt_min, mt_max, 'GeV')
    mt.setBins(n_bins)
    # Manually add the boundaries to it as python attributes for easy access
    mt.mt_min = mt_min
    mt.mt_max = mt_max
    return mt


def get_mt_from_th1(histogram, name=None):
    """
    Returns mT from the x axis of a TH1 histogram.
    Min and max are simply the left/right boundary of the first/last bin,
    and bin width is copied.
    """
    mt = get_mt(
        histogram.GetBinLowEdge(1),
        histogram.GetBinLowEdge(histogram.GetNbinsX()+1),
        histogram.GetNbinsX(),
        name = uid() if name is None else name
        )
    object_keeper.add(mt)
    return mt


def trigeff_expression(year=2018, max_fit_range=1000.):
    """
    Returns a TFormula-style expression that represents the trigger
    efficiency as a function of MT.

    The formula contains a switch based on `max_fit_range`: evaluating
    beyond `max_fit_range` will return 1. only.
    (This is needed because the fit is unstable under extrapolation)
    """
    import requests
    parameters = np.array(requests.get(
        'https://raw.githubusercontent.com/boostedsvj/triggerstudy/main/bkg/bkg_trigeff_fit_{}.txt'
        .format(year)).json())
    expr = sigmoid(poly1d(parameters))
    return '({0})*(@0<{1}) + (@0>={1})'.format(expr, max_fit_range)

def poly1d(parameters, mt_par='@0'):
    degree = len(parameters)
    return '+'.join([ '{}*pow({},{})'.format(p, mt_par, degree-i-1) for i, p in enumerate(parameters)])

def sigmoid(expr):
    return '1./(1.+exp(-({})))'.format(expr)

class PDF(object):
    """
    Container object for a RooParametricShapeBinPdf, with more info
    """
    def __init__(self):
        pass

    def __repr__(self):
        return (
            '<RooParametricShapeBinPdf "{}"'
            '\n  pdf_type   = {}'
            '\n  n_pars     = {}'
            '\n  expression = "{}"'
            '\n  pdf        = "{}"'
            '\n  mt         = "{}"'
            '\n  th1        = "{}"'
            '\n  rgp        = "{}"'
            '\n  parameters = \n    {}'
            '\n  >'
            .format(
                self.name, self.pdf_type, self.n_pars, self.expression,
                self.pdf.GetName(), self.mt.GetName(), self.th1.GetName(), self.rgp.GetName(),
                '\n    '.join(['"'+p.GetName()+'"' for p in self.parameters])
                )
            )

    def evaluate(self, x_vals, varname=None):
        """
        Equivalent to the y_values of pdf.createHistogram('...', mt).
        Result is normalized to 1.
        """
        variable = self.mt if varname is None else self.pdf.getVariables()[varname]
        y = []
        for x in x_vals:
            variable.setVal(x)
            y.append(self.pdf.getVal())
        y = np.array(y)
        return y / (y.sum() if y.sum()!=0. else 1.)


def pdf_factory(pdf_type, n_pars, mt, bkg_th1, name=None, mt_scale='1000', trigeff=None):
    """
    Main factory entry point to generate a single RooParametricShapeBinPDF on a TH1.

    If `trigeff` equals 2016, 2017, or 2018, the bkg trigger efficiency as a 
    function of mT_AK15_subl is prefixed to the expression.
    """
    if pdf_type not in known_pdfs(): raise Exception('Unknown pdf_type %s' % pdf_type)
    if name is None: name = uid()
    #logger.info(
    #    'Building name={} pdf_type={} n_pars={} mt.GetName()="{}", bkg_th1.GetName()="{}"'
    #    .format(name, pdf_type, n_pars, mt.GetName(), bkg_th1.GetName())
    #    )
    expression = all_pdfs[pdf_type].expression(n_pars, mt_scale)
    if trigeff in [2016, 2017, 2018]:
        logger.info('Adding trigger efficiency formula to expression')
        expression = '({})/({})'.format(expression, trigeff_expression(trigeff))
    parameters = all_pdfs[pdf_type].parameters(n_pars, name)
    #logger.info(
    #    'Expression: {}; Parameter names: {}'
    #    .format(expression, ', '.join(p.GetName() for p in parameters))
    #    )
    generic_pdf = ROOT.RooGenericPdf(
        name+'_rgp', name+'_rgp',
        expression, ROOT.RooArgList(mt, *parameters)
        )
    object_keeper.add(generic_pdf)
    parametric_shape_bin_pdf = ROOT.RooParametricShapeBinPdf(
        name+'_rpsbp', name+'_rpsbp',
        generic_pdf, mt, ROOT.RooArgList(*parameters), bkg_th1
        )
    object_keeper.add(parametric_shape_bin_pdf)
    pdf = PDF()
    pdf.name = name
    pdf.pdf = parametric_shape_bin_pdf
    pdf.rgp = generic_pdf
    pdf.expression = expression # Tag it onto the instance
    pdf.parameters = parameters
    pdf.n_pars = n_pars
    pdf.th1 = bkg_th1
    pdf.pdf_type = pdf_type
    pdf.mt = mt
    #logger.info('Created {}'.format(pdf))
    return pdf


def pdfs_factory(pdf_type, mt, bkg_th1, name=None, mt_scale='1000', trigeff=None, npars=None):
    """
    Like pdf_factory, but returns a list for all available n_pars
    """
    if name is None: name = uid()
    all_n_pars = range(all_pdfs[pdf_type].n_min, all_pdfs[pdf_type].n_max+1)
    if npars is not None: all_n_pars = [npars]
    return [ pdf_factory(pdf_type, n_pars, mt, bkg_th1, name+'_npars'+str(n_pars), mt_scale, trigeff=trigeff) for n_pars in all_n_pars]


def to_list(rooarglist):
    return [rooarglist.at(i) for i in range(rooarglist.getSize())]


def get_variables(rooabsarg):
    """
    Returns a list of all variables a RooAbsArg depends on
    """
    argset = ROOT.RooArgList(rooabsarg.getVariables())
    return [argset.at(i) for i in range(argset.getSize())]


def set_pdf_to_fitresult(pdf, res):
    """
    Sets the parameters of a pdf to the fit result. 
    """
    def set_par(par, value):
        par.setRange(value-10., value+10.)
        par.setVal(value)
    import scipy # type: ignore
    if isinstance(res, ROOT.RooFitResult):
        vals = []
        for p_fit, p_pdf in zip(to_list(res.floatParsFinal()), pdf.parameters):
            set_par(p_pdf, p_fit.getVal())
            vals.append(p_fit.getVal())
        return vals
    elif isinstance(res, scipy.optimize.optimize.OptimizeResult):
        for val, p_pdf in zip(res.x, pdf.parameters):
            set_par(p_pdf, val)
        return res.x


def plot_fits(pdfs, fit_results, data_obs, outfile='test.pdf'):
    """
    Plots the fitted bkg pdfs on top of the data histogram.
    """
    # First find the mT Roo variable in one of the pdfs
    mT = pdfs[0].mt
    mT_min = mT.getMin()
    mT_max = mT.getMax()

    # Open the frame
    xframe = mT.frame(ROOT.RooFit.Title("extended ML fit example"))
    c1 = ROOT.TCanvas()
    c1.cd()

    # Plot the data histogram
    data_obs.plotOn(xframe, ROOT.RooFit.Name("data_obs"))

    # Set to fitresult
    for pdf, res in zip(pdfs, fit_results): set_pdf_to_fitresult(pdf, res)

    # Plot the pdfs
    colors = [ROOT.kPink+6, ROOT.kBlue-4, ROOT.kRed-4, ROOT.kGreen+1]
    colors.extend(colors)
    colors.extend(colors)
    for pdf, color in zip(pdfs, colors):
        pdf.pdf.plotOn(
            xframe,
            ROOT.RooFit.Name(pdf.pdf.GetName()),
            ROOT.RooFit.LineColor(color),
            ROOT.RooFit.Range("Full")
            )

    # Add the fit result text labels
    for i, fit_result in enumerate(fit_results):
        n_fit_pars = len(fit_result.floatParsFinal())
        chi2 = xframe.chiSquare(pdfs[i].pdf.GetName(), "data_obs", n_fit_pars)

        par_values = [ 'p{}={:.3f}'.format(i, v.getVal()) for i, v in enumerate(pdfs[i].parameters)]
        par_value_str = ', '.join(par_values)
        txt = ROOT.TText(
            .12, 0.12+i*.05,
            "model {}, nP {}, chi2: {:.4f}, {}".format(i, n_fit_pars, chi2, par_value_str)
            )
        txt.SetNDC()
        txt.SetTextSize(0.04)
        txt.SetTextColor(colors[i])
        xframe.addObject(txt)
        txt.Draw()

    xframe.SetMinimum(0.0002)
    xframe.Draw()
    c1.SetLogy()
    c1.SaveAs(outfile)
    c1.SaveAs(outfile.replace('.pdf', '.png'))
    del xframe, c1


def pdf_ploton_frame(frame, pdf, norm):
    pdf.plotOn(
        frame,
        ROOT.RooFit.Normalization(norm, ROOT.RooAbsReal.NumEvent),
        ROOT.RooFit.LineColor(ROOT.kBlue),
        ROOT.RooFit.FillColor(ROOT.kOrange),
        ROOT.RooFit.FillStyle(1001),
        ROOT.RooFit.DrawOption("L"),
        ROOT.RooFit.Name(pdf.GetName()),
        ROOT.RooFit.Range("Full")
        )
    pdf.paramOn(
        frame,
        ROOT.RooFit.Label(pdf.GetTitle()),
        ROOT.RooFit.Layout(0.45, 0.95, 0.94),
        ROOT.RooFit.Format("NEAU")
        )


def data_ploton_frame(frame, data, is_data=True):
    data_graph = data.plotOn(
        frame,
        ROOT.RooFit.DataError(ROOT.RooAbsData.Poisson if is_data else ROOT.RooAbsData.SumW2),
        ROOT.RooFit.DrawOption("PE0"),
        ROOT.RooFit.Name(data.GetName())
        )
    return data_graph


def get_chi2_viaframe(mt, pdf, data):
    """
    Get the chi2 value of the fitted pdf on data by plotting it on
    a temporary frame.

    For some reason this is much faster than the non-plotting method.
    """
    logger.debug('Using plotOn residuals')
    frame = mt.frame(ROOT.RooFit.Title(""))
    pdf_ploton_frame(frame, pdf.pdf, norm=data.sumEntries())
    data_graph = data_ploton_frame(frame, data)
    roochi2 = frame.chiSquare(pdf.pdf.GetName(), data.GetName(), pdf.n_pars)
    # Number of degrees of freedom: data will contain zeros of mt binning
    # is finer than original data binning; don't count those zeros
    dhist = frame.findObject(data.GetName(),ROOT.RooHist.Class())
    n_bins = 0
    for i in range(dhist.GetN()):
        x = ctypes.c_double(0.)
        y = ctypes.c_double(0.)
        dhist.GetPoint(i,x,y)
        if y!=0: n_bins += 1
    ndf = n_bins - pdf.n_pars
    chi2 = roochi2 * ndf
    roopro = ROOT.TMath.Prob(chi2, ndf)
    return {'roochi2': roochi2, 'chi2': chi2, 'roopro': roopro, 'ndf': ndf, 'n_bins': n_bins}


def get_rss_viaframe(mt, pdf, data):
    """
    Get the Residual Sum of Squares (RSS) of the fitted pdf on data by plotting it on
    a temporary frame.

    if `return_n_bins` is True, also the number of bins that were used to calculate the
    RSS.

    For some reason this is much faster than the non-plotting method.
    """
    pdf = pdf.pdf
    logger.info('Calculating RSS using plotOn residuals')
    rss = 0.
    frame = mt.frame(ROOT.RooFit.Title(""))
    pdf_ploton_frame(frame, pdf, norm=data.sumEntries())
    data_graph = data_ploton_frame(frame, data)

    hist = data_graph.getHist()
    residuals = frame.residHist(data.GetName(), pdf.GetName(), False, True) # this is y_i - f(x_i)
    xmin, xmax = array('d', [0.]), array('d', [0.])
    data.getRange(mt, xmin, xmax)

    n_bins = 0
    for i in range(0, hist.GetN()): # type:ignore
        x, y = hist.GetX()[i], hist.GetY()[i]
        res_y = residuals.GetY()[i]
        left  = x - hist.GetErrorXlow(i)
        right = x + hist.GetErrorXhigh(i)
        if left > xmax[0] and right > xmax[0]: continue
        elif y <= 0.: continue
        if logger.level <= logging.DEBUG:
            y_pdf = y - res_y
            logger.debug(
                '{i} ({left:.2f} to {right:.2f}):'
                # '\n  pdf  : {val_pdf:8.3f}'
                '\n  data : {y:8.3f}'
                '\n  residual : {res_y:8.3f}'
                '\n  pdf : {y_pdf:8.3f}'
                .format(**locals())
                )
        rss += res_y**2
        n_bins += 1
    rss = sqrt(rss)
    logger.info('rss_viaframe: {}'.format(rss))
    return {'rss': rss, 'n_bins': n_bins}


def do_fisher_test(mt, data, pdfs, a_crit=.07, gof_type='rss'):
    """
    Does a Fisher test. First computes the cl_vals for all combinations
    of pdfs, then picks the winner.

    Returns the pdf that won.
    """
    gof_fns = {
        'chi2': get_chi2_viaframe,
        'rss': get_rss_viaframe,
    }
    gofs = [ gof_fns[gof_type](mt, pdf, data) for pdf in pdfs ]
    # Compute test values of all combinations beforehand
    cl_vals = {}
    for i in range(len(pdfs)-1):
        for j in range(i+1, len(pdfs)):
            n1 = pdfs[i].n_pars
            n2 = pdfs[j].n_pars
            gof1         = gofs[i][gof_type]
            gof2, n_bins = gofs[j][gof_type], gofs[j]['n_bins']
            f = ((gof1-gof2)/(n2-n1)) / (gof2/(n_bins-n2))
            cl = 1.-ROOT.TMath.FDistI(f, n2-n1, n_bins-n2)
            cl_vals[(i,j)] = cl

    def get_winner(i, j):
        if i >= j: raise Exception('i must be smaller than j')
        a_test = cl_vals[(i,j)]
        n_pars_i = pdfs[i].n_pars
        n_pars_j = pdfs[j].n_pars
        if a_test > a_crit:
            # Null hypothesis is that the higher n_par j is not significantly better.
            # Null hypothesis is not rejected
            logger.info(
                f'Comparing n_pars={n_pars_i} with n_pars={n_pars_j}:'
                f' a_test={a_test:.4f} > {a_crit};'
                f' null hypothesis NOT rejected, n_pars={n_pars_i} wins'
                )
            return i
        else:
            # Null hypothesis is rejected, the higher n_par j pdf is significantly better
            logger.info(
                f'Comparing n_pars={n_pars_i} with n_pars={n_pars_j}:'
                f' a_test={a_test:.4f} < {a_crit};'
                f' null hypothesis REJECTED, n_pars={n_pars_j} wins'
                )
            return j

    # get_winner = lambda i, j: i if cl_vals[(i,j)] > a_crit else j

    logger.info('Running F-test')
    winner = get_winner(0,1)
    for i in range(2,len(pdfs)):
        winner = get_winner(winner, i)
    logger.info(f'Winner is pdf {winner} with {pdfs[winner].n_pars} parameters')

    # Print the table
    table = [[''] + [f'{p.n_pars}' for p in pdfs[1:]]]
    for i in range(len(pdfs)-1):
        a_test_vals = [f'{pdfs[i].n_pars}'] + ['' for _ in range(len(pdfs)-1)]
        for j in range(i+1, len(pdfs)):
            a_test_vals[j] = f'{cl_vals[(i,j)]:9.7f}'
        table.append(a_test_vals)
    logger.info('alpha_test values of pdf i vs j:\n' + tabelize(table))

    tex_table = copy.deepcopy(table)
    tex_table[0][0] = '\\# of pars'
    for row in tex_table:
        for i in range(len(row)-1,0,-1):
            row.insert(i, '&')
        row.append('\\\\')
    tex_table.insert(1, ['\\hline'])
    logger.info(f'Tex-formatted table:\n{tabelize(tex_table)}')

    return winner

# _______________________________________________________________________
# For combine

class Datacard:

    @classmethod
    def from_txt(cls, txtfile):
        return read_dc(txtfile)

    def __init__(self):
        self.shapes = [] # Expects len-4 or len-5 iterables as elements
        self.channels = [] # Expects len-2 iterables as elements, (name, rate)
        self.rates = OrderedDict() # Expects str as key, OrderedDict as value
        self.systs = []
        self.extargs = []

    def __eq__(self, other):
        return (
            self.shapes == other.shapes
            and self.channels == other.channels
            and self.rates == other.rates
            and self.systs == other.systs
            and self.extargs == other.extargs
            )

    @property
    def syst_names(self):
        return [s[0] for s in self.systs]

    def syst_rgx(self, rgx):
        """
        Returns a list of all systematics that match a pattern
        (Uses Unix file-like pattern matching, e.g. 'bkg_*')
        """
        import fnmatch
        return [s for s in self.syst_names if fnmatch.fnmatch(s, rgx)]


def read_dc(datacard):
    """
    Returns a Datacard object based on the txt stored in the passed path
    """
    with open(datacard, 'r') as f:
        dc = read_dc_txt(f.read())
    dc.filename = datacard
    return dc


def read_dc_txt(txt):
    """
    Returns a Datacard object based on the passed datacard txt.
    """
    lines = txt.split('\n')
    dc = Datacard()

    blocks = []
    block = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        elif line.startswith('#'):
            continue
        elif line.startswith('---------------'):
            blocks.append(block)
            block = []
            continue
        block.append(line)
    blocks.append(block)
    if len(blocks) != 5:
        raise Exception('Found {} blocks, expected 5'.format(len(blocks)))

    # Shapes are easy
    for line in blocks[1]: dc.shapes.append(line.split()[1:])
    # pprint(dc.shapes)

    # Channels
    channel_table = transpose([ l.split() for l in blocks[2] ])[1:]
    for name, rate in channel_table:
        dc.channels.append((name, int(rate)))
    # pprint(dc.channels)

    # Rates
    rate_table = transpose([ l.split() for l in blocks[3] ])[1:]
    for ch, name, index, rate in rate_table:
        dc.rates.setdefault(ch, OrderedDict())
        dc.rates[ch][name] = int(rate)
    # pprint(dc.rates)

    # Systs and extargs
    for line in blocks[4]:
        syst = line.split()
        if len(syst) >= 3:
            try:
                syst[2] = float(syst[2])
            except:
                pass
        if len(syst)>1 and syst[1]=='extArg':
            dc.extargs.append(syst)
        else:
            dc.systs.append(syst)
    # pprint(dc.systs)
    # pprint(dc.extargs)

    return dc


def parse_dc(dc):
    '''
    Very basic datacard formatter
    '''
    txt = ''
    line = '\n' + '-'*83

    txt += (
        'imax {} number of channels'
        '\njmax * number of backgrounds'
        '\nkmax * number of nuisance parameters'
        ).format(len(dc.channels))
    txt += line

    # Careful with workspace path: Should be relative path to DC
    shapes = copy.copy(dc.shapes)
    for i in range(len(shapes)):
        shapes[i][2] = osp.basename(shapes[i][2])

    txt += '\n' + tabelize([['shapes']+s for s in shapes])
    txt += line
    txt += '\n' + tabelize(transpose([('bin', 'observation')] + list(dc.channels)))
    txt += line

    # Format the bin/process table
    # Format per column, and only transpose at str format time
    proc_nr_dict = {}
    proc_nr_counter = [0]
    def proc_nr(proc):
        if not proc in proc_nr_dict:
            proc_nr_dict[proc] = proc_nr_counter[0]
            proc_nr_counter[0] += 1
        return proc_nr_dict[proc]
    table = [['bin', 'process', 'process', 'rate']]
    for bin in dc.rates:
        for proc in dc.rates[bin]:
            table.append([bin, proc, proc_nr(proc), int(dc.rates[bin][proc])])
    txt += '\n' + tabelize(transpose(table))

    txt += line
    txt += '\n' + tabelize(dc.systs)
    txt += '\n'
    return txt


def transpose(l):
    '''Transposes a list of lists'''
    return list(map(list, zip(*l)))


def tabelize(data):
    '''
    Formats a list of lists to a single string (space separated).
    Rows need not be of same length.
    '''
    # Ensure data is strings
    data = [ [ str(i) for i in row ] for row in data ]
    # Determine the row with the most columns
    n_columns = max(map(len, data))
    # Determine how wide each column should be (max)
    col_widths = [0 for i in range(n_columns)]
    for row in data:
        for i_col, item in enumerate(row):
            if len(item) > col_widths[i_col]:
                col_widths[i_col] = len(item)
    # Format
    return '\n'.join(
        ' '.join(
            format(item, str(w)) for item, w in zip(row, col_widths)
            )
        for row in data
        )


def make_multipdf(pdfs, name='roomultipdf'):
    cat = ROOT.RooCategory('pdf_index', "Index of Pdf which is active")
    pdf_arglist = ROOT.RooArgList()
    for pdf in pdfs: pdf_arglist.add(pdf.pdf)
    multipdf = ROOT.RooMultiPdf(name, "All Pdfs", cat, pdf_arglist)
    multipdf.cat = cat
    multipdf.pdfs = pdfs
    norm_theta = ROOT.RooRealVar(name+'_theta', "Extra component", 0.01, -100., 100.)
    norm = ROOT.RooFormulaVar(name+'_norm', "1+0.01*@0", ROOT.RooArgList(norm_theta))
    norm_objs = [norm_theta, norm]
    object_keeper.add_multiple([cat, norm_objs, multipdf])
    return multipdf, norm_objs


def compile_datacard_macro(bkg_pdf, data_obs, sig, outfile='dc_bsvj.txt', systs=None, syst_th1s=None):
    do_syst = systs is not None
    w = ROOT.RooWorkspace("SVJ", "workspace")

    def commit(thing, *args, **kwargs):
        name = thing.GetName() if hasattr(thing, 'GetName') else '?'
        logger.info('Importing {} ({})'.format(name, thing))
        getattr(w, 'import')(thing, *args, **kwargs)

    # Bkg pdf: May be multiple
    is_multipdf = hasattr(bkg_pdf, '__len__')
    if is_multipdf:
        mt = bkg_pdf[0].mt
        multipdf, norm = make_multipdf(bkg_pdf)
        commit(multipdf.cat)
        for n in norm: commit(n)
        commit(multipdf)
    else:
        mt = bkg_pdf.mt
        commit(bkg_pdf, ROOT.RooFit.RenameVariable(bkg_pdf.GetName(), 'bkg'))

    commit(data_obs)
    commit(sig, ROOT.RooFit.Rename('sig'))

    if syst_th1s is not None:
        for th1 in syst_th1s:
            # th1.SetName(th1.GetName().replace(sig.GetName(), 'sig'))
            name = th1.GetName().replace(sig.GetName(), 'sig')
            dh = ROOT.RooDataHist(name, name, ROOT.RooArgList(mt), th1)
            commit(dh)

    wsfile = outfile.replace('.txt', '.root')
    dump_ws_to_file(wsfile, w)

    # Write the dc
    dc = Datacard()
    dc.shapes.append(['roomultipdf' if is_multipdf else 'bkg', 'bsvj', wsfile, 'SVJ:$PROCESS'])
    dc.shapes.append(['sig', 'bsvj', wsfile, 'SVJ:$PROCESS'] + (['SVJ:$PROCESS_$SYSTEMATIC'] if do_syst else []))
    dc.shapes.append(['data_obs', 'bsvj', wsfile, 'SVJ:$PROCESS'])
    dc.channels.append(('bsvj', int(data_obs.sumEntries())))
    dc.rates['bsvj'] = OrderedDict()
    dc.rates['bsvj']['sig'] = sig.sumEntries()
    dc.rates['bsvj']['roomultipdf' if is_multipdf else 'bkg'] = data_obs.sumEntries()
    # Freely floating bkg parameters
    def systs_for_pdf(pdf):
        for par in pdf.parameters:
            dc.systs.append([par.GetName(), 'flatParam'])
    [systs_for_pdf(p) for p in multipdf.pdfs] if is_multipdf else systs_for_pdf(bkg_pdf)
    # Rest of the systematics
    if is_multipdf:
        dc.systs.append([multipdf.cat.GetName(), 'discrete'])
        for n in norm:
            if n.InheritsFrom("RooRealVar"): dc.systs.append([n.GetName(), 'flatParam'])
    if do_syst: dc.systs.extend(systs)
    txt = parse_dc(dc)

    logger.info('txt datacard:\n%s', txt)
    logger.info('Dumping txt to ' + outfile)
    if not osp.isdir(osp.dirname(outfile)): os.makedirs(osp.dirname(outfile))
    with open(outfile, 'w') as f:
        f.write(txt)


def camel_to_snake(name):
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


class CombineCommand(object):

    comma_separated_args = [
        '--freezeParameters',
        '--trackParameters',
        '--trackErrors',
        '--named',
        ]
    comma_separated_arg_map = { camel_to_snake(v.strip('-')) : v for v in comma_separated_args }
    comma_separated_arg_map['redefine_signal_pois'] = '--redefineSignalPOIs'

    def __init__(self, dc=None, method='MultiDimFit', args=None, kwargs=None, pass_through=None, exe='combine'):
        self.exe = exe
        self.dc = dc
        self.method = method
        self.args = set() if args is None else args
        self.kwargs = OrderedDict()
        if kwargs: self.kwargs.update(kwargs)
        for key in self.comma_separated_arg_map: setattr(self, key, set())
        self.parameters = OrderedDict()
        self.parameter_ranges = OrderedDict()
        self.pass_through = [] if pass_through is None else pass_through
        self.pdf_pars = []

    def get_name_key(self):
        """
        'name' parameter for the combine CLI can be either '-n' or '--name';
        ensure consistency
        """
        assert ('-n' in self.kwargs) + ('--name' in self.kwargs) < 2
        if '-n' in self.kwargs:
            return '-n'
        else:
            return '--name'

    @property
    def name(self):
        return self.kwargs.get(self.get_name_key(), '')

    @name.setter
    def name(self, val):
        self.kwargs[self.get_name_key()] = val

    @property
    def seed(self):
        if '-s' in self.kwargs:
            return self.kwargs['-s']
        elif self.kwargs.get('-t', -1) >= 0:
            return 123456
        return None

    @property
    def outfile(self):
        out = 'higgsCombine{0}.{1}.mH120.root'.format(self.name, self.method)
        if self.seed is not None:
            print(self.seed)
            out = out.replace('.root', '.{}.root'.format(self.seed))
            print(out)
        return out

    @property
    def logfile(self):
        return self.outfile.replace('.root','') + '.log'

    def copy(self):
        return copy.deepcopy(self)

    def __repr__(self):
        return '<CombineCommand ' + '\n    '.join(self.parse()) + '\n    >'

    @property
    def str(self):
        return ' '.join(self.parse())

    def add_range(self, name, left, right):
        self.parameter_ranges[name] = [left, right]

    def set_parameter(self, name, value, left=None, right=None):
        self.parameters[name] = value
        if left is not None and right is not None: self.add_range(name, left, right)

    def pick_pdf(self, pdf):
        """
        Picks the background pdf. Freezes pdf_index, and freezes parameters of any other
        pdfs in the datacard.
        """
        logger.info('Using pdf %s', pdf)
        self.set_parameter('pdf_index', known_pdfs().index(pdf))
        pdf_pars = self.dc.syst_rgx('bsvj_bkgfit%s_npars*' % pdf)
        pars_to_track = ['r', 'n_exp_final_binbsvj_proc_roomultipdf', 'shapeBkg_roomultipdf_bsvj__norm', 'roomultipdf_theta'] + pdf_pars
        self.track_parameters.update(pars_to_track)
        self.track_errors.update(pars_to_track)
        self.freeze_parameters.add('pdf_index')
        self.pdf_pars = ['roomultipdf_theta'] + pdf_pars
        for other_pdf in known_pdfs():
            if other_pdf==pdf: continue
            other_pdf_pars = self.dc.syst_rgx('bsvj_bkgfit%s_npars*' % other_pdf)
            self.freeze_parameters.update(other_pdf_pars)
        # extargs do not have errors
        self.track_parameters.update([x[0] for x in self.dc.extargs])

    def asimov(self, flag=True):
        """
        Turns on Asimov settings.
        """
        if flag:
            logger.info('Doing asimov')
            self.kwargs['-t'] = -1
            self.args.add('--toysFrequentist')
            self.name += 'Asimov'
        else:
            logger.info('Doing observed')
            self.name += 'Observed'
            if read_arg("--tIsToyIndex", default=False, action="store_true").tIsToyIndex:
                toy_index = read_arg('-t', type=int).t
                self.name += f'Toy{toy_index}'

    def configure_from_command_line(self, scan=False):
        """
        Configures the CombineCommand based on command line parameters.
        sys.argv is reset to its original state at the end of the function.
        """
        with reset_sys_argv():
            asimov = pull_arg('-a', '--asimov', action='store_true').asimov
            self.asimov(asimov)

            logger.info('Taking pdf from command line (default is ua2)')
            pdf = pull_arg('--pdf', type=str, choices=known_pdfs(), default='ua2').pdf
            self.pick_pdf(pdf)

            # special settings to seed fits for likelihood scan
            # rand > 0 performs random fits; rand = 0 just seeds fit w/ prev values; rand < 0 disables this behavior
            rand = pull_arg('--rand', type=int, default=0).rand
            ext = pull_arg('--ext', type=str, default="").ext
            range_factor = pull_arg('--range-factor', type=str, default="err").range_factor
            if scan and rand>=0:
                self.kwargs['--pointsRandProf'] = rand
                self.kwargs['--saveSpecifiedNuis'] = ','.join(self.pdf_pars)
                self.args.add('--saveSpecifiedNuisErrors')
                if len(ext)>0:
                    self.kwargs['--setParameterRandomInitialValueRanges'] = ':'.join([p+'=ext,'+range_factor for p in self.pdf_pars])
                    self.kwargs['--extPointsFile'] = ext
                else:
                    self.kwargs['--setParameterRandomInitialValueRanges'] = ':'.join([p+'=prev,'+range_factor for p in self.pdf_pars])

            toyseed = pull_arg('-t', type=int).t
            if toyseed:
                if asimov: raise Exception('asimov and -t >-1 are exclusive options')
                self.kwargs['-t'] = toyseed
                self.args.add('--toysFrequentist')
                self.kwargs['-s'] = 123456 # The default combine seed

            seed = pull_arg('-s', '--seed', type=int).seed
            if seed is not None: self.kwargs['-s'] = seed

            self.kwargs['-v'] = pull_arg('-v', '--verbosity', type=int, default=0).verbosity

            normRange = pull_arg('--normRange', type=float, nargs=2, default=[]).normRange
            if len(normRange)>0:
                self.add_range('shapeBkg_roomultipdf_bsvj__norm', normRange[0], normRange[1])

            expectSignal = pull_arg('--expectSignal', type=float).expectSignal
            if expectSignal is not None: self.kwargs['--expectSignal'] = expectSignal

            # Pass-through: Anything that's left is simply tagged on to the combine command as is
            self.pass_through = ' '.join(sys.argv[1:])


    def parse(self):
        """
        Returns the command as a list
        """
        command = [self.exe]
        command.append('-M ' + self.method)
        if not self.dc: raise Exception('self.dc must be a valid path')
        command.append('-d ' + self.dc.filename)
        command.extend(list(self.args))
        # handle kwargs that can be used multiple times
        command.extend([' '.join([k+' '+str(vv) for vv in v]) if isinstance(v,list) else k+' '+str(v) for k, v in self.kwargs.items()])

        for attr, command_str in self.comma_separated_arg_map.items():
            values = getattr(self, attr)
            if not values: continue
            command.append(command_str + ' ' + ','.join(list(sorted(values))))

        if self.parameters:
            strs = ['{0}={1}'.format(k, v) for k, v in self.parameters.items()]
            command.append('--setParameters ' + ','.join(strs))

        if self.parameter_ranges:
            strs = ['{0}={1},{2}'.format(parname, *ranges) for parname, ranges in self.parameter_ranges.items()]
            command.append('--setParameterRanges ' + ':'.join(strs))

        if self.pass_through: command.append(self.pass_through)

        return command


def bestfit(cmd, range=None):
    """
    Takes a CombineComand, and applies options on it to turn it into
    MultiDimFit best-fit command
    """
    cmd = cmd.copy()
    cmd.method = 'MultiDimFit'
    cmd.args.add('--saveWorkspace')
    cmd.args.add('--saveNLL')
    cmd.redefine_signal_pois.add('r')
    cmd.kwargs['--X-rtd'] = 'REMOVE_CONSTANT_ZERO_POINT=1'
    if range is None: range = pull_arg('-r', '--range', type=float, default=[-3., 5.], nargs=2).range
    cmd.add_range('r', range[0], range[1])
    # Possibly delete some settings too
    cmd.kwargs.pop('--algo', None)
    return cmd


def scan(cmd, range=None):
    """
    Takes a CombineComand, and applies options on it to turn it into
    scan over r
    """
    cmd = bestfit(cmd, range)
    cmd.kwargs['--algo'] = 'grid'
    cmd.kwargs['--alignEdges'] = 1
    cmd.track_parameters.add('r')
    cmd.kwargs['--points'] = pull_arg('-n', type=int, default=100).n
    return cmd


def gen_toys(cmd):
    """
    Takes a base CombineCommand, applies options to generate toys
    """
    cmd = cmd.copy()
    cmd.method = 'GenerateOnly'
    cmd.args.add('--saveToys')
    cmd.args.add('--bypassFrequentistFit')
    cmd.args.add('--saveWorkspace')
    # Possibly delete some settings too
    cmd.kwargs.pop('--algo', None)
    cmd.track_parameters = set()
    return cmd

def fit_toys(cmd):
    # cmdFit="combine ${DC_NAME_ALL}
    #    -M FitDiagnostics
    #    -n ${fitName}
    #    --toysFile higgsCombine${genName}.GenerateOnly.mH120.123456.root
    #    -t ${nTOYS}
    #    -v
    #    -1
    #    --toysFrequentist
    #    --saveToys
    #    --expectSignal ${expSig}
    #    --savePredictionsPerToy
    #    --bypassFrequentistFit
    #    --X-rtd MINIMIZER_MaxCalls=100000
    #    --setParameters $SetArgFitAll
    #    --freezeParameters $FrzArgFitAll
    #    --trackParameters $TrkArgFitAll"
    #    --rMin ${rMin}
    #    --rMax ${rMax}

    cmd = cmd.copy()
    cmd.method = 'FitDiagnostics'
    cmd.kwargs.pop('--algo', None)
    cmd.args.add('--toysFrequentist')
    cmd.args.add('--saveToys')
    cmd.args.add('--savePredictionsPerToy')
    cmd.args.add('--bypassFrequentistFit')
    cmd.kwargs['--X-rtd'] = 'MINIMIZER_MaxCalls=100000'

    toysFile = pull_arg('--toysFile', required=True, type=str).toysFile
    cmd.kwargs['--toysFile'] = toysFile

    if not '-t' in cmd.kwargs:
        with open_root(toysFile) as f:
            cmd.kwargs['-t'] = f.Get('limit').GetEntries()

    return cmd


def likelihood_scan_factory(
    datacard,
    rmin=0., rmax=2., n=40,
    verbosity=0, asimov=False,
    pdf_type='ua2',
    n_toys=None,
    raw=None,
    ):
    """
    Returns a good CombineCommand template for a likelihood scan 
    """
    dc = read_dc(datacard)
    cmd = CombineCommand(datacard, 'MultiDimFit', raw=raw)

    cmd.redefine_signal_pois.append('r')
    cmd.add_range('r', rmin, rmax)
    cmd.track_parameters.extend(['r'])

    cmd.args.add('--saveWorkspace')
    cmd.args.add('--saveNLL')
    cmd.kwargs['--algo'] = 'grid'
    cmd.kwargs['--points'] = n
    cmd.kwargs['--X-rtd'] = 'REMOVE_CONSTANT_ZERO_POINT=1'
    cmd.kwargs['-v'] = verbosity

    if asimov:
        if n_toys is not None: raise Exception('asimov and n_toys are exclusive')
        cmd.kwargs['-t'] = '-1'
        cmd.args.add('--toysFreq')
        cmd.kwargs['-n'] = 'Asimov'
    else:
        cmd.kwargs['-n'] = 'Observed'

    if n_toys is not None: cmd.kwargs['-t'] = str(n_toys)

    cmd.pick_pdf(pdf_type)

    return cmd


@contextmanager
def switchdir(other_dir):
    if other_dir:
        try:
            current_dir = os.getcwd()
            logger.info('Changing into %s', other_dir)
            os.chdir(other_dir)
            yield other_dir
        finally:
            logger.info('Changing back into %s', current_dir)
            os.chdir(current_dir)
    else:
        try:
            yield None
        finally:
            pass


def run_command(cmd, chdir=None):
    if DRYMODE:
        logger.warning('DRYMODE: ' + cmd)
        return '<drymode - no stdout>'

    with switchdir(chdir):
        logger.warning('Issuing command: ' + cmd)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            shell=True,
            )
        output = []
        for stdout_line in iter(process.stdout.readline, ""):
            subprocess_logger.info(stdout_line.rstrip("\n"))
            output.append(stdout_line)
        process.stdout.close()
        process.wait()
        returncode = process.returncode

        if returncode == 0:
            logger.info("Command exited with status 0 - all good")
        else:
            logger.error("Exit status {0} for command: {1}".format(returncode, cmd))
            raise subprocess.CalledProcessError(cmd, returncode)
        return output


def run_combine_command(cmd, chdir=None, logfile=None):
    if chdir:
        # Fix datacard to be an absolute path first
        cmd = cmd.copy()
        cmd.dc = osp.abspath(cmd.dc)
    logger.info('Running {0}'.format(cmd))
    out = run_command(cmd.str, chdir)
    if logfile is not None:
        with open(logfile, 'w') as f:
            f.write(''.join(out))
    return out


# _______________________________________________________________________
# ROOT and RooFit interface

@contextmanager
def open_root(path, mode='READ'):
    '''Context manager that takes care of closing the ROOT file properly'''
    tfile = None
    try:
        tfile = ROOT.TFile.Open(path, mode)
        yield tfile
    finally:
        if tfile is not None: tfile.Close()


class ROOTObjectKeeper:
    """
    Keeps ROOT objects in it so ROOT doesn't garbage collect them.
    """
    def __init__(self):
        self.objects = {}

    def add(self, thing):
        try:
            key = thing.GetName()
        except AttributeError:
            key = str(uuid.uuid4())
        if key in self.objects:
            logger.warning('Variable %s (%s) already in object keeper', thing.GetName(), thing.GetTitle())
        self.objects[key] = thing

    def add_multiple(self, things):
        for t in things: self.add(t)


object_keeper = ROOTObjectKeeper()


def get_arrays(rootfile, treename='limit'):
    """Poor man's uproot: make a dict `branch_name : np.array of values`"""
    with open_root(rootfile) as f:
        tree = f.Get(treename)
        branch_names = [ b.GetName() for b in tree.GetListOfBranches() ]
        r = { b : [] for b in branch_names }
        for entry in tree:
            for b in branch_names:
                r[b].append(getattr(entry, b))
    return {k : np.array(v) for k, v in r.items()}


def get_ws(f, wsname=None):
    """
    Functionality to grab the first workspace that's encountered in a rootfile
    """
    if wsname is None:
        # Pick the first one
        for key in f.GetListOfKeys():
            obj = f.Get(key.GetName())
            if isinstance(obj, ROOT.RooWorkspace):
                ws = obj
                break
        else:
            f.ls()
            raise Exception('No workspace found in {0}'.format(f))
    else:
        ws = f.Get(wsname)
    return ws


def th1_to_hist(h):
    n_bins = h.GetNbinsX()
    # GetBinLowEdge of the right overflow bin is the high edge of the actual last bin
    return AttrDict(
        binning = np.array([h.GetBinLowEdge(i) for i in range(1,n_bins+2)]),
        vals = np.array([h.GetBinContent(i) for i in range(1,n_bins+1)]),
        errs = np.array([h.GetBinError(i) for i in range(1,n_bins+1)])
        )


def th1_binning_and_values(h, return_errs=False):
    """
    Returns the binning and values of the histogram.
    Does not include the overflows.
    """
    import inspect
    logger.warning('DEPRECATED: Use th1_to_hist instead; called by {}'.format(inspect.stack()[1][3]))
    n_bins = h.GetNbinsX()
    # GetBinLowEdge of the right overflow bin is the high edge of the actual last bin
    binning = np.array([h.GetBinLowEdge(i) for i in range(1,n_bins+2)])
    values = np.array([h.GetBinContent(i) for i in range(1,n_bins+1)])
    errs = np.array([h.GetBinError(i) for i in range(1,n_bins+1)])
    return (binning, values, errs) if return_errs else (binning, values)


def th1_to_datahist(histogram, mt=None):
    if mt is None: mt = get_mt_from_th1(histogram)
    datahist = ROOT.RooDataHist(uid(), '', ROOT.RooArgList(mt), histogram, 1.)
    datahist.mt = mt
    return datahist


def binning_from_roorealvar(x):
    binning = [x.getMin()]
    for i in range(x.numBins()):
        binning.append(binning[-1] + x.getBinWidth(i))
    return np.array(binning)


def roodataset_values(data, varname='mt'):
    """
    Works on both RooDataHist and RooDataSet!
    """
    x = []
    y = []
    dy = []
    for i in range(data.numEntries()):
        s = data.get(i)
        x.append(s[varname].getVal())
        y.append(data.weight())
        dy.append(data.weightError())
    return np.array(x), np.array(y), np.array(dy)


def pdf_values(pdf, x_vals, varname='mt'):
    """
    Equivalent to the y_values of pdf.createHistogram('...', mt)
    """
    variable = pdf.getVariables()[varname]
    y = []
    for x in x_vals:
        variable.setVal(x)
        # logger.info('{}: set {} to {}'.format(pdf.GetName(), variable.GetName(), x))
        # logger.info('  got pdf = {}'.format(pdf.getVal()))
        y.append(pdf.getVal())
    y = np.array(y)
    return y / (y.sum() if y.sum()!=0. else 1.)
