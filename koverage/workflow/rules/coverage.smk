rule read_r1:
    """Read the R1 file"""
    input:
        lambda wildcards: samples.reads[wildcards.sample]["R1"]
    output:
        pipe(os.path.join(dir.temp, "{sample}.R1.fastq"))
    threads:
        config.resources.pipe.cpu
    resources:
        mem_mb = config.resources.pipe.mem_mb,
        time = config.resources.pipe.time_min
    params:
        lambda wildcards: "zcat" if samples.reads[wildcards.sample]["R1"].endswith(".gz") else "cat"
    group:
        "pipejob"
    shell:
        """
        {params} {input} >> {output}
        """


rule sam_to_counts:
    """Collect the counts for each contig from the piped SAM output"""
    input:
        os.path.join(dir.temp,"{sample}.sam"),
    output:
        tsv = temp(os.path.join(dir.temp, "{sample}.counts.tsv")),
        sam = pipe(os.path.join(dir.temp, "{sample}.depth.sam"))
    threads:
        config.resources.pipe.cpu
    resources:
        mem_mb = config.resources.pipe.mem_mb,
        time = config.resources.pipe.time_min
    group:
        "pipejob"
    script:
        os.path.join(dir.scripts, "samToCounts.py")


if config.args.bams:
    rule mpileup_save_bam:
        input:
            os.path.join(dir.temp,"{sample}.depth.sam")
        output:
            mp = pipe(os.path.join(dir.temp,"{sample}.mpileup")),
            bm = os.path.join(dir.bam,"{sample}.bam")
        threads:
            config.resources.pipe.cpu
        resources:
            mem_mb=config.resources.pipe.mem_mb,
            time=config.resources.pipe.time_min
        conda:
            os.path.join(dir.env, "minimap.yaml")
        group:
            "pipejob"
        shell:
            """
            samtools view -b {input} \
                | tee {output.bm} \
                | samtools mpileup >> {output.mp}
            """
else:
    rule mpileup:
        input:
            os.path.join(dir.temp,"{sample}.depth.sam")
        output:
            pipe(os.path.join(dir.temp,"{sample}.mpileup"))
        threads:
            config.resources.pipe.cpu
        resources:
            mem_mb=config.resources.pipe.mem_mb,
            time=config.resources.pipe.time_min
        conda:
            os.path.join(dir.env, "minimap.yaml")
        group:
            "pipejob"
        shell:
            """
            samtools view -b {input} \
                | samtools mpileup -Aa - \
                | cut -f1,4 >> {output}
            """


rule mpileup_to_depth:
    """Collect depth histograms for each contig for sample and echo sam output"""
    input:
        os.path.join(dir.temp,"{sample}.mpileup")
    output:
        hist = temp(os.path.join(dir.temp,"{sample}.depth.tsv")), # todo: keep or delete?
        kurt = temp(os.path.join(dir.temp, "{sample}.kurtosis.tsv"))
    threads:
        config.resources.pipe.cpu
    resources:
        mem_mb = config.resources.pipe.mem_mb,
        time = config.resources.pipe.time_min
    params:
        config.args.maxDepth
    group:
        "pipejob"
    script:
        os.path.join(dir.scripts, "mpileupToDepth.py")


rule sample_coverage:
    """convert raw counts to RPKM, FPKM, TPM, etc values"""
    input:
        tsv = os.path.join(dir.temp,"{sample}.counts.tsv"),
        r1 = os.path.join(dir.temp,"{sample}.R1.count"),
        kurt = os.path.join(dir.temp, "{sample}.kurtosis.tsv")
    output:
        temp(os.path.join(dir.temp,"{sample}.cov.tsv"))
    threads: 1
    script:
        os.path.join(dir.scripts, "sampleCoverage.py")


rule all_sample_coverage:
    """Concatenate the sample coverage TSVs"""
    input:
        expand(os.path.join(dir.temp,"{sample}.cov.tsv"), sample=samples.names)
    output:
        os.path.join(dir.result,"sample_coverage.tsv")
    threads: 1
    shell:
        """
        printf "Sample\tContig\tRPM\tRPKM\tRPK\tTPM\tKurtosis\n" > {output}
        cat {input} >> {output}
        """


rule combine_coverage:
    """Combine all sample coverages"""
    input:
        os.path.join(dir.result,"sample_coverage.tsv")
    output:
        all_cov = os.path.join(dir.result, "all_coverage.tsv"),
        sample_sum = os.path.join(dir.result, "sample_summary.tsv"),
        all_sum = os.path.join(dir.result, "all_summary.tsv")
    threads: 1
    script:
        os.path.join(dir.scripts, "combineCoverage.py")
