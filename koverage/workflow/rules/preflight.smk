import attrmap as ap
import glob
import os


# Concatenate Snakemake's own log file with the master log file
def copy_log_file():
    files = glob.glob(os.path.join(".snakemake", "log", "*.snakemake.log"))
    if not files:
        return None
    current_log = max(files, key=os.path.getmtime)
    shell("cat " + current_log + " >> " + config.args.log)

onsuccess:
    copy_log_file()

onerror:
    copy_log_file()


# DIRECTORIES
dir = ap.AttrMap()
dir.base = workflow.basedir
dir.env = os.path.join(dir.base, "envs")
dir.scripts = os.path.join(dir.base, "scripts")

try:
    assert(ap.utils.to_dict(config.args)["output"]) is not None
    dir.out = config.args.output
except (KeyError, AssertionError):
    dir.out = "koverage.out"

dir.temp = os.path.join(dir.out, "temp")
dir.log = os.path.join(dir.out, "logs")
dir.paf = os.path.join(dir.out, "pafs")
dir.hist = os.path.join(dir.out, "histograms")
dir.result = os.path.join(dir.out, "results")
dir.bench = os.path.join(dir.out, "benchmarks")


config.refkmers = os.path.join(dir.temp, os.path.basename(config.args.ref) + "." + str(config.args.kmer_size) + "mer.zst")


# PARSE SAMPLES
include: os.path.join(dir.base, config.modules[config.args.library]["preflight"])

samples = ap.AttrMap()
samples.reads = parseSamples(config.args.reads)
samples.names = list(ap.utils.get_keys(samples.reads))
samples = au.convert_state(samples, read_only=True)


# LIBRARY SPECIFIC RULES
include: os.path.join(dir.base, config.modules[config.args.library]["mapping"])
include: os.path.join(dir.base, config.modules[config.args.library]["kmer"])


# TARGETS
targets = ap.AttrMap()

if config.args.pafs:
    targets.pafs = expand(os.path.join(dir.paf,"{sample}.paf.zst"), sample=samples.names)
else:
    targets.pafs = []

targets.coverage = [
    os.path.join(dir.result, "sample_coverage.tsv"),
    # os.path.join(dir.result, "all_coverage.tsv"),
    # os.path.join(dir.result, "sample_summary.tsv"),
    # os.path.join(dir.result, "all_summary.tsv")
]

targets.kmercov = [
    # os.path.join(dir.result, "sample_kmer_coverage.tsv"),
    # config.refkmers,
    # expand(os.path.join(dir.temp, "{sample}." + str(config.args.kmer_size) + "{file}"), sample=samples.names, file=["mer","mer.kcov.zst"]),
    os.path.join(dir.result, "sample_kmer_coverage." + str(config.args.kmer_size) + "mer.tsv.gz")
]
