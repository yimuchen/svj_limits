
#!/bin/bash
#==============================================================================
# run_limit_asimov_sig_inj.sh --------------------------------------------------
#------------------------------------------------------------------------------
# Author(s): Brendan Regnery --------------------------------------------------
#------------------------------------------------------------------------------
# Basic functionality:
#   Creates limits with expected 'asimov' values with an observed limit of an
#      asimov toy with signal injected at 350 GeV (same toy for all mass points)
#------------------------------------------------------------------------------
# To run fully: ./run_limit_asimov_sig_inj.sh 
# To run with a specfic date: ./run_limit_asimov_sig_inj.sh --mMed_values "300 350"

# Default values
hists_date="20241115"  # Date of the histograms used for making datacards
dc_date=$(date +%Y%m%d)    # Dynamically set today's date 
scan_date=$(date +%Y%m%d)  
sel="bdt=0.67"
siginj=0.2
mInj=350
mDark_value="10"
rinv_value="0p3"
mMed_values=(200 250 300 350 400 450 500 550)
run_only_fits=false        # Default to running all loops
only_inj=false

# Parse command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -d) toys_date="$2"; shift ;; 
        -f) run_only_fits=true ;; # option to only run likelihood scan
        --only_inj) only_inj=true ;; # only generates and fits 'observed' signal injected asimov toy
        --sel) sel="$2"; shift ;;
        --hists_date) hists_date="$2"; shift ;;
        --siginj) siginj="$2"; shift ;;
        --mInj) mInj="$2"; shift ;;
        --mDark) mDark_value="$2"; shift ;;
        --rinv) rinv_value="$2"; shift ;; 
        --mMed_values) IFS=' ' read -r -a mMed_values <<< "$2"; shift ;;  # Parse mMed_values as array
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# if you want to generate cards and the asimov toy
if [ "$run_only_fits" = false ]; then

  # Generate the datacards
  for mMed in "${mMed_values[@]}"
  do
    # Generate datacards for the current mMed value with variable mDark and hists_date
    python3 cli_boosted.py gen_datacards \
      --bkg hists/merged_${hists_date}/bkg_sel-${sel}.json \
      --sig hists/smooth_${hists_date}/SVJ_s-channel_mMed-${mMed}_mDark-${mDark_value}_rinv-${rinv_value}_alpha-peak_MADPT300_13TeV-madgraphMLM-pythia8_sel-${sel}_smooth.json
  done

  # Generate an asimov toy with a signal at requested Zprime mass
  python3 cli_boosted.py gentoys \
    dc_${dc_date}_${sel}/dc_SVJ_s-channel_mMed-${mInj}_mDark-${mDark_value}_rinv-${rinv_value}_alpha-peak_MADPT300_13TeV-madgraphMLM-pythia8_sel-${sel}_smooth.txt  \
    -t -1 \
    --expectSignal ${siginj} \
    -s 1001

  # Likelihood scan for expected limits
  # These become the 'asimov' files
  if [ "$only_inj" = false ]; then
    python3 cli_boosted.py likelihood_scan_mp \
      dc_${dc_date}_${sel}/dc*mDark-${mDark_value}_rinv-${rinv_value}*${sel}*smooth.txt \
      --range 0.0 2.0 \
      --seed 1001 \
      --asimov
  fi

fi

# Run the multiple file likelihood scan on the asimov toy with injected signal
# These become the 'observed' files
python3 cli_boosted.py likelihood_scan_mp \
  dc_${dc_date}_${sel}/dc*mDark-${mDark_value}_rinv-${rinv_value}*${sel}*smooth.txt \
  --range 0.0 2.0 \
  --seed 1001 \
  --toysFile toys_${dc_date}/higgsCombineObserveddc_SVJ_s-channel_mMed-${mInj}_mDark-${mDark_value}_rinv-${rinv_value}_alpha-peak_MADPT300_13TeV-madgraphMLM-pythia8_sel-${sel}_smooth.GenerateOnly.mH120.1001.root \
  -t -1

# plot
python3 quick_plot.py brazil \
  scans_${dc_date}/higgsCombine*rinv-${rinv_value}*.root \
  -o asimov_${mInj}_sig_test_${rinv_value}_mdark${mDark_value}.pdf


