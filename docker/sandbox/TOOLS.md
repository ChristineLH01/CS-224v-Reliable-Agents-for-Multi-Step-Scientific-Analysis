# Preinstalled software in this sandbox

All of this is in the active conda environment `bio` (`/opt/conda/envs/bio`), already on PATH.
The exact solved versions are listed in `/opt/sandbox/conda-env.lock.yml`.

- Python 3.12: numpy, pandas, scipy, scikit-learn, statsmodels, matplotlib, seaborn,
  pyarrow, biopython, pysam, pyBigWig, pyfaidx, scanpy, anndata, h5py, python-igraph,
  leidenalg, pydeseq2, gseapy
- R (`R`, `Rscript`): data.table, DESeq2, edgeR, limma
- Reads / alignment / quantification: samtools, bcftools, htslib (bgzip, tabix), bedtools,
  minimap2, bwa, bowtie2, hisat2, STAR, salmon, kallisto, subread (featureCounts), fastp,
  FastQC, cutadapt, seqkit, gffread
- Homology / annotation: BLAST+, MMseqs2, DIAMOND
- Epigenomics / tracks / motifs: deepTools, MACS3, HOMER, MEME suite,
  UCSC bigWigToBedGraph / bedGraphToBigWig / liftOver
- Variants / population genetics: vcftools, plink2
- General: git, curl, wget, unzip, zstd, pigz, jq, build-essential

Install more with `mamba install -y -c conda-forge -c bioconda <package>` or `pip install <package>`.
Reference data (genomes, annotations, databases) is not preinstalled; download what you need
from the allowed domains.
