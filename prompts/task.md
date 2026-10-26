Answer the following bioinformatics question by analyzing the data provided.

## Question

{question}

## Data and environment

- The input data for this question is in `{data_dir}` ({n_files}). Your working directory is `{workdir}`; put any intermediate files there.
- You are in a Linux container with {cpus} CPUs and {memory} of RAM (no GPU). Common bioinformatics tools are preinstalled in the active conda environment; `/opt/sandbox/TOOLS.md` lists them. You can install more with `mamba install -y -c conda-forge -c bioconda <package>` or `pip install <package>`.
- Internet access is limited to these domains and their subdomains: {allowed_domains}.
- You have about {time_limit_min} minutes of wall-clock time in total. Work autonomously; nobody will answer questions.

## Rules

- Looking up GEO/SRA/ENA/BioProject accession IDs to identify the original dataset, source publication, or study metadata is **not allowed**.
- Standard bioinformatics database use (gene ID lookup, sequence annotation, reference genome download, BLAST) **is allowed**.

## Answer format

When you are done, end your final message with your answer inside answer tags, like this: `<answer>your answer</answer>`. Give one specific answer, in the form the question asks for.
