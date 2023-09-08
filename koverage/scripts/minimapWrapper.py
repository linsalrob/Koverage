#!/usr/bin/env python3


"""Run minimap2, parse its output, calculate counts on the fly

This script will run minimap2 of a sample's reads against the reference FASTA.
We use a wrapper instead of a snakemake rule to avoid additional read/writes for every sample.
PAF files of alignments can optionally be saved.

- `worker_mm_to_count_paf_queues` - read minimap2 output and pass to queues for processing and saving PAF
- `worker_mm_to_count_queues` - read minimap2 output and pass to queue for processing only
- `worker_paf_writer` - read minimap2 output from queue and write to zstandard-zipped file
- `worker_count_and_print` - read minimap2 output from queue, calculate counts, print to output files
- `build_mm2cmd` - return the minimap2 command based on presence of R2 file
- `start_workers` - start queues and worker threads
"""


import subprocess
import threading
import queue
import numpy as np
import os
import logging
import sys
import zstandard as zstd


def worker_mm_to_count_paf_queues(pipe, count_queue, paf_queue):
    """Read minimap2 output and slot into queues for collecting coverage counts, and saving the paf file.

    Args:
        pipe (pipe): minimap2 pipe for reading
        count_queue (Queue): queue for putting for counts
        paf_queue (Queue): queue for putting for saving paf
    """

    for line in iter(pipe.stdout.readline, b""):
        line = line.decode()
        count_queue.put(line)
        paf_queue.put(line)

    for q in [count_queue, paf_queue]:
        q.put(None)


def worker_mm_to_count_queues(pipe, count_queue):
    """Read minimap2 output and slot into queues for collecting coverage counts

    Args:
    pipe (pipe): minimap2 pipe for reading
    count_queue (Queue): queue for putting for counts
    """

    for line in iter(pipe.stdout.readline, b""):
        count_queue.put(line.decode())

    count_queue.put(None)


def worker_paf_writer(paf_queue, paf_dir, sample, chunk_size=100):
    """Read minimap2 output from queue and write to zstd-zipped file

    Args:
        paf_queue (Queue): queue of minimap2 output for reading
        paf_dir (str): dir for saving paf files
    """

    cctx = zstd.ZstdCompressor()
    os.makedirs(paf_dir, exist_ok=True)
    output_f = open(os.path.join(paf_dir, sample + ".paf.zst"), "wb")
    lines = []

    while True:
        line = paf_queue.get()
        if line is None:
            break
        lines.append(line.encode())
        if len(lines) >= chunk_size:
            compressed_chunk = cctx.compress(b"".join(lines))
            output_f.write(compressed_chunk)
            lines = []

    if lines:
        compressed_chunk = cctx.compress(b"".join(lines))
        output_f.write(compressed_chunk)
        output_f.flush()

    output_f.close()


def contig_lens_from_fai(file_path):
    """Collect the sequence IDs from the reference fasta file

    Args:
        file_path (str): File path of reference fasta fai index file

    Returns:
        ctg_lens (dict):
            key: Sequence ID (str)
            value: contig length (int)
    """
    ctg_lens = dict()
    with open(file_path, 'r') as in_fai:
        for line in in_fai:
            l = line.strip().split()
            if len(l) == 5:
                ctg_lens[l[0]] = int(l[1])
    return ctg_lens


def print_output(output_queue, **kwargs):
    """Print the output lines to the output file

    Args:
        output_queue (Queue): queue of lines ready for printing
    """
    with open(kwargs["output_counts"], "w") as out_counts:
        while True:
            line = output_queue.get()
            if line is not None:
                out_counts.write(line)
            else:
                break


def calculate_metrics(bin_queue, output_queue, **kwargs):
    """Calculate count metrics from bin histograms

    Args:
        bin_queue (Queue): queue of contitg bin counts
        output_queue (Queue): output lines for writing
    """
    while True:
        contig = bin_queue.get()
        if contig is None:
            break

        ctg_mean = "{:.{}g}".format(np.mean(contig[3]), 4)
        ctg_median = "{:.{}g}".format(np.median(contig[3]), 4)
        ctg_hitrate = "{:.{}g}".format((len(contig[3]) - contig[3].count(0)) / len(contig[3]), 4)
        contig[3] = [x / kwargs["bin_width"] for x in contig[3]]
        if len(contig[3]) > 1:
            ctg_variance = "{:.{}g}".format(np.variance(contig[3]), 4)
        else:
            ctg_variance = "{:.{}g}".format(0, 4)
        line = "\t".join([
            contig[0],
            str(contig[1]),
            str(contig[2]),
            ctg_mean,
            ctg_median,
            ctg_hitrate,
            ctg_variance + "\n",
        ])
        output_queue.put(line)
    output_queue.put(None)


def worker_count_and_print(count_queue, bin_queue, contig_lengths, **kwargs):
    """Collect the counts from minimap2 queue and calc counts on the fly

    Args:
        count_queue (Queue): queue of minimap2 output for reading
        bin_queue (Queue): queue of bin counts
        contig_lengths (dict):
            key: Sequence ID (str)
            value: contig length (int)
        **kwargs (dict):
            - bin_width (int): Width of bins for hitrate and variance estimation
            - output_counts (str): filepath for writing output count stats
            - output_lib (str): filepath for writing library size
    """

    contig_counts = dict()
    contig_bin_counts = dict()
    total_count = 0

    for seq_id in contig_lengths.keys():
        contig_counts[seq_id] = 0
        contig_bin_counts[seq_id] = [0] * (int(int(contig_lengths[seq_id]) / kwargs["bin_width"]) + 1)

    while True:
        line = count_queue.get()

        try:
            l = line.strip().split()
        except AttributeError:
            break

        contig_counts[l[5]] += 1

        contig_bin_counts[l[5]][int(int(l[7]) / kwargs["bin_width"])] += 1
        total_count += 1

    for c in contig_counts.keys():
        bin_queue.put([c,contig_lengths[c],contig_counts[c],contig_bin_counts[c]])

    # close the queue
    for _ in range(kwargs["threads"]):
        bin_queue.put(None)

    with open(kwargs["output_lib"], "w") as out_lib:
        out_lib.write(f"{str(total_count)}\n")


def build_mm2cmd(**kwargs):
    """Return the minimap2 command

    Args:
        **kwargs (dict):
            - threads (int): Number of worker threads to use
            - minimap_mode (str): Mapping preset for minimap2
            - ref_idx (str): Reference indexed file
            - r1_file (str): Forward reads file
            - r2_file (str): Reverse reads file (or "" for SE reads/longreads)

    Returns:
        mm2cmd (list): minimap2 command for opening with subprocess
    """

    mm2cmd = [
        "minimap2",
        "-t",
        str(kwargs["threads"]),
        "-x",
        kwargs["minimap_mode"],
        "--secondary=no",
        kwargs["ref_idx"],
        kwargs["r1_file"],
    ]

    if kwargs["r2_file"] and kwargs["r2_file"].lower() not in ["none", "null", ""]:
        mm2cmd.append(kwargs["r2_file"])

    return mm2cmd


def start_workers(queue_counts, queue_paf, pipe_minimap, **kwargs):
    """Start workers for reading the minimap output and parsing to queue(s) for processing

    Args:
        queue_counts (Queue): queue to use for putting minimap2 output for collecting counts
        pipe_minimap (pipe): subprocess pipe for minimap2 for reading
        **kwargs (dict):
            - paf_file (str): PAF file for writing
            - save_pafs (bool): flag for if PAF files should be saved
    """

    thread_parser_paf = None

    if kwargs["save_pafs"]:
        thread_reader = threading.Thread(
            target=worker_mm_to_count_paf_queues,
            args=(pipe_minimap, queue_counts, queue_paf),
        )
        thread_reader.start()
        thread_parser_paf = threading.Thread(
            target=worker_paf_writer,
            args=(queue_paf, kwargs["paf_dir"], kwargs["sample"]),
        )
        thread_parser_paf.start()
    else:
        thread_reader = threading.Thread(
            target=worker_mm_to_count_queues, args=(pipe_minimap, queue_counts)
        )
        thread_reader.start()

    return thread_reader, thread_parser_paf


def main(**kwargs):
    if kwargs["pyspy"]:
        subprocess.Popen(
            [
                "py-spy",
                "record",
                "-s",
                "-o",
                kwargs["pyspy_svg"],
                "--pid",
                str(os.getpid()),
            ]
        )

    logging.basicConfig(filename=kwargs["log_file"], filemode="w", level=logging.DEBUG)
    mm2cmd = build_mm2cmd(**kwargs)
    logging.debug(f"Starting minimap2: {' '.join(mm2cmd)}\n")
    pipe_minimap = subprocess.Popen(
        mm2cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # Create queue for counts
    queue_counts = queue.Queue()
    queue_paf = queue.Queue()
    thread_reader, thread_parser_paf = start_workers(
        queue_counts, queue_paf, pipe_minimap, **kwargs
    )

    # Contig IDs and lens from fai
    contig_lens = contig_lens_from_fai(kwargs["ref_fai"])

    # Queue for contig bin counts and output lines
    queue_bins = queue.Queue()
    queue_output = queue.Queue()

    # Spawn workers for calculating metrics and printing output
    metric_worker_threads = []
    thread = threading.Thread(target=print_output, args=(queue_output,), kwargs=kwargs)
    thread.start()
    metric_worker_threads.append(thread)
    for i in range(kwargs["threads"]):
        thread = threading.Thread(target=calculate_metrics, args=(queue_bins, queue_output,), kwargs=kwargs)
        thread.start()
        metric_worker_threads.append(thread)

    # Read from q2 and get read counts
    thread_parser_counts = threading.Thread(
        target=worker_count_and_print, args=(queue_counts, queue_bins, contig_lens,), kwargs=kwargs
    )
    thread_parser_counts.start()

    # wait for workers to finish
    thread_parser_counts.join()
    if thread_parser_paf:
        thread_parser_paf.join()

    # check minimap2 finished ok
    pipe_minimap.stdout.close()
    pipe_minimap.wait()
    if pipe_minimap.returncode != 0:
        logging.debug(f"\nERROR: Pipe failure for:\n{' '.join(mm2cmd)}\n")
        logging.debug(f"STDERR: {pipe_minimap.stderr.read().decode()}")
        sys.exit(1)

    # Join other threads
    thread_reader.join()
    for thread in metric_worker_threads:
        thread.join()


if __name__ == "__main__":
    main(
        threads=snakemake.threads,
        log_file=snakemake.log.err,
        minimap_mode=snakemake.params.minimap,
        ref_idx=snakemake.input.ref,
        ref_fai=snakemake.input.fai,
        r1_file=snakemake.input.r1,
        r2_file=snakemake.params.r2,
        save_pafs=snakemake.params.pafs,
        paf_dir=snakemake.params.paf_dir,
        sample=snakemake.wildcards.sample,
        bin_width=snakemake.params.bin_width,
        output_counts=snakemake.output.counts,
        output_lib=snakemake.output.lib,
        pyspy=snakemake.params.pyspy,
        pyspy_svg=snakemake.log.pyspy,
    )
