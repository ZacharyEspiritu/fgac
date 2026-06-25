# Artifact for "Plaintext Recovery Against Post-Filtering Access Control"

This is the associated artifact for the USENIX Security 2026 paper "Plaintext
Recovery Against Post-Filtering Access Control".

This repository is structured as a family of two independent CLI tools called
`unfilter-*`, each of which provides a command-line utility for running attack
benchmarks against various classes of databases:

|        Artifact | Paper Scope                            |      Tool      |        Documentation Start         |
| --------------: | :------------------------------------- | :------------: | :--------------------------------: |
| [`rls/`](./rls) | **Section 3: Row-Level Security**      | `unfilter-rls` | [`rls/README.md`](./rls/README.md) |
| [`dls/`](./dls) | **Section 4: Document-Level Security** | `unfilter-dls` | [`dls/README.md`](./dls/README.md) |

Both artifacts are designed to be extensible and usable for not just future
research projects, but also for industry practitioners who are interested in
seeing how applicable these attacks may be in their setup or system. Each
artifact contains documentation on how to add new evaluation system
functionality (in addition to regression tests and strong code typing and
formatting requirements) as well as how to extend it to other scenarios.

Please see [`rls/README.md`](./rls/README.md) and
[`dls/README.md`](./dls/README.md) for deeper information on each artifact. For
evaluators, we suggest starting with the
["Suggested Evaluation Flow"](#suggested-evaluation-flow) below.

## Suggested Evaluation Flow

For convenience, we provide a suggested evaluation flow for those who are
looking to reproduce our results.

### RLS (`unfilter-rls`)

The `unfilter-rls` utility provides implementations of the attacks and benchmarks 
for the RLS section of the paper. It also has an built-in Google Compute Engine 
integration for spawning and orchestrating experiment VMs automatically.

We have tested our RLS setup on fresh Linux (e.g. Debian 13, Ubuntu 24)
operating systems. It also works on macOS. 

#### Setup (5 human-minutes)

The easiest way to reproduce the RLS experiments is to use `unfilter-rls`'s
Google Compute Engine integration, which will use any local `gcloud` CLI
credentials to automatically deploy and orchestrate experiment VMs for you.
This requires Google Compute Engine credentials (and billing to
pay for the VMs). 

> [!IMPORTANT]
> For convenience during the artifact evaluation, we provide credentials
> for you to access a VM authenticated to our own Google Compute Engine instance
> (see the comment on HotCRP for details). SSH to that instance, then follow the instructions below. 

To start, clone the
repo:

```bash
git clone https://github.com/ZacharyEspiritu/fgac
cd fgac/rls
```

Install the necessary dependencies:

```bash
bash setup.sh
```

`setup.sh` installs the `unfilter-rls` command. If your current shell cannot
find it, open a new terminal or source your shell startup file, such as
`~/.zshrc`, `~/.bashrc`, or `~/.profile`.

#### Kick-the-Tires (5 human-minutes + 20 compute-minutes)

To make sure your setup is working and that your account has the ability to
provision Google Compute Engine VMs via the Google Compute Engine API, first 
check that your environment has been properly setup:

```bash
unfilter-rls doctor
```

If all rows print "OK", then you can run a quick experiment (**C-R9**, the
fastest experiment):

```bash
unfilter-rls claims run C-R9
```

This will take about 20 compute-minutes to run (most of this time is spent
provisioning and tearing down Google Cloud VMs). 
When the experiment is done, verify that the numbers align with the
statistics reported in the "Schema and data" paragraph in Section 3.3 of the
paper. If this works, you have validated (**C-R9**) and you should be ready to 
run all the experiments.

> [!NOTE]
> **Optional (see information about `claims run` instances):** You can see some information
> about experiment runs by running
> 
> ```bash
> unfilter-rls results list
> ```
> 
> This will show you a table of all the experiment runs you have run, organized by
> a unique `RUN_ID`. You can inspect the specific run using
> 
> ```bash
> unfilter-rls results inspect [RUN_ID]
> ```
> 
> When the run is finished, `unfilter-rls results list` should show the run with
> status `success`. Also, the experiment should have written a file to
> `rls/results/dbsize/summary.txt`.


#### Run All Experiments (5 human-minutes + 36 compute-hours)

The simplest way to run all of the experiments is in a single command. Some of
the experiments run for a long time, so we **strongly recommend** using `screen`
to run the scripts since it will let them keep running if your SSH window closes
for any reason:

```bash
# Run the experiments in detached mode.
screen -dm unfilter-rls claims run 1,2,3,4,5,6,7,8,9
# Reattach to the experiment script.
screen -r
```

> [!TIP]
> **Helpful Hints for `screen`:** When attached to a `screen` instance, you can
> press <kbd>Ctrl</kbd> + <kbd>a</kbd> then <kbd>d</kbd> to detach from the
> session to let things keep running in the background. If you would like to
> scroll the terminal window, you can enter _scroll mode_ by pressing
> <kbd>Ctrl</kbd> + <kbd>a</kbd> then <kbd>Esc</kbd>, then use <kbd>Ctrl</kbd> +
> <kbd>u</kbd> (up) and <kbd>Ctrl</kbd> + <kbd>d</kbd> to scroll; to exit
> _scroll mode_, press <kbd>Esc</kbd>.

> [!NOTE]
> **Optional (change experiment options):** You can also run each claim individually by changing the list of arguments
> passed to `unfilter-rls claims run`. (If you are using our Google Cloud
> infrastructure, we ask that you do not run multiple experiments in parallel to
> reduce the number of VMs we need to spawn at once.) There are various configuration
> options you can use to modify the experiment runs; see `rls/README.md` and the
> Artifact Appendix for more details.

We suggest launching the command with `screen` and checking again in 36 hours for
the results.
You can monitor the experiment run over time using the `screen` guide above or by
using `unfilter-rls results list`; when the run is finished, 
`unfilter-rls results list` should show the run with status `success`.

#### Validate Results (15 human-minutes)

When the experiment run is finished, cross-check the claims as described in the Artifact Appendix. 

For
convenience, the RLS artifact provides a `rls/latex` directory which compiles
the figures in a LaTeX PDF for easier viewing. Once you're done with running the
experiments, you can `scp` the generated LaTeX `main.pdf` PDF to your local
machine to easily view the set of all the figures you generated. See our comment
on HotCRP for more details.
Using the PDF, you can validate each of the claims as follows:

- (**C-R1** / **E-R1**) Compare Figure 2 in `main.pdf` to Figure 2 in the paper.

  > If you would like to trace where this figure comes from, the generated
  > figure in `main.pdf` is imported from
  > `results/existence/existence_kde_figure.tex`.

- (**C-R2** / **E-R2**) Compare Figure 3 in `main.pdf` to Figure 3 in the paper.

  > The generated figure in `main.pdf` is imported from
  > `results/range/existence_range_kde_figure.tex`.

- (**C-R3** / **E-R3**) Compare Table 1 in `main.pdf` to Table 1 in the paper.
  Please be aware that due to rate limiting that we cannot control in this
  specific GCE environment, less load than anticipated may occur on higher CPU
  loads (e.g., 75%, 85%, and 95%) for the _inline_ policy which may result in
  higher accuracy than expected for those rows. The main focus of this claim is
  on the _join_ experiments, which should have about the same accuracy as what
  is in the paper.

  > The generated figure in `main.pdf` is imported from
  > `results/table1/table1.pgf`, which can be viewed separately at
  > `results/table1/table1.pdf`.

- (**C-R4** / **E-R4**) Compare Table 2 in `main.pdf` to Table 2 in the paper.

  > The generated figure in `main.pdf` is imported from
  > `results/table2/table2.pgf`, which can be viewed separately at
  > `results/table2/table2.pdf`.

- (**C-R5** / **E-R5**) Compare Figure 5 in `main.pdf` to Figure 2 in the paper,
  and compare Figure 6 in `main.pdf` to Figure 3 in the paper. (There are no
  figures in the paper specifically associated with the Transparent Data
  Encryption experiments, but the claim is that the experiment should produce
  figures that look very similar to the non-TDE results in Figure 2 and Figure
  3.)

  > The generated figures in `main.pdf` are imported from
  > `results/existence/existence_tde_figure.tex` and
  > `results/range/existence_range_tde_figure.tex`.

- (**C-R6** / **E-R6**) Compare Table 3 in `main.pdf` to Table 3 in the paper. For the higher
  parallelism experiments (workers > 4), the additional variance and binary search nature of the
  attack means that accuracy may be occasionally
  lower than the averages reported in the paper (e.g.
  if the attack got unlucky at a higher tree level and
  mistakenly pruned much of the search space). (This
  is why we run each row 10 times in the paper.)

  > The generated figure in `main.pdf` is imported from
  > `results/table3/table3.tex`.

- (**C-R7** / **E-R7**) Compare Table 4 in `main.pdf` to Table 4 in the paper. For the reconstruction orderings starting with `zip` and `age`, you
  sometimes will get significantly higher _Recall_ and _Precision_ than what is in the paper due to inherent
  variance in the binary search and oracle accuracy,
  but this should come at the cost of significantly
  higher _Queries_ and _Time_ because the lack of accuracy results in more false positives, resulting in
  unnecessary trie traversals. (This is why we run
  each row 3 times in the paper.) The most important factor to check for is that `ssn`-leading orderings should
  outperform the `zip`- or `age`-leading orderings in either _Recall_ / _Precision_, or _Queries_ / _Time_.

  > The generated figure in `main.pdf` is imported from
  > `results/table4/table4.tex`.

- (**C-R8** / **E-R8**) Compare Table 5 in `main.pdf` to Table 5 in the paper.

  > The generated figure in `main.pdf` is imported from
  > `results/table5/table5.pgf`, which can be viewed separately at
  > `results/table5/table5.pdf`.

- (**C-R9** / **E-R9**) This is not included in the `main.pdf`, but you should
  have already validated this as part of the "Kick-the-Tires" step above.

### DLS (`unfilter-dls`)

The `unfilter-dls` utility provides implementations of the attacks and benchmarks 
for the DLS section of the paper. It also provides a Docker integration script for
running the experiments locally on a single host.

#### Setup (5 human-minutes)

The entirety of the DLS artifact can be run locally on a single machine. We have 
tested our DLS setup on fresh Linux (e.g. Debian 13, Ubuntu 24) operating systems. 

> [!IMPORTANT]
> **Reminder:** For the artifact evaluation, we **strongly encourage** evaluators to
> run the DLS experiments on their own
> setup. Evaluators should not attempt to run the DLS experiments on the entry point VM
> provided on HotCRP—that VM is a minimal VM that does not have enough memory to run the
> DLS experiments.

To start, clone the repo:

```bash
git clone https://github.com/ZacharyEspiritu/fgac
cd fgac/dls
```

Install the necessary dependencies:

```bash
bash setup.sh
```

`setup.sh` installs the `unfilter-dls` command. If your current shell cannot
find it, open a new terminal or source your shell startup file, such as
`~/.zshrc`, `~/.bashrc`, or `~/.profile`.

#### Kick-the-Tires (5 human-minutes)

As a sanity check, check that the experiments work on the smallest dataset:

```bash
bash run.sh --datasets 1
```

This should produce `results/reviewer/opensearch_table.tex` and
`results/reviewer/elasticsearch_table.tex`. The rows should match the first row
of Table 6 in the paper. If this works, you should be ready.

#### Run All Experiments (5 human-minutes + 16–18 compute-hours)

Run all of the experiments:

```bash
bash run.sh
```

For convenience, the script will cache results for dataset runs after they have
successfully completed (e.g., in case you accidentally stop the pipeline). If
you would like to rerun existing results, add the
`--destructive-rerun-existing-results` flag.

> [!NOTE]
> **Optional: shorter experiments with less memory overhead.** The |D| = 1000
> run requires substantial memory overhead (~64 GB) and 6–8 hours of compute
> time. To reduce the run time to 2–3 compute-hours and reduce the memory
> overhead (<10 GB), you can skip the longer |D| = 1000 run by only running
> against the |D| ∈ {1, 10, 100} datasets:
>
> ```bash
> bash run.sh --datasets 1,10,100
> ```

#### Validate Results (5 human-minutes)

You can validate each of the claims as follows:

- (**C-D1** / **E-D1**) Verify that all columns in
  `results/reviewer/opensearch_table.tex` are similar to Table 6 in the paper. In particular, "Logical Queries" should be
  identical.

- (**C-D2** / **E-D2**) Verify that all columns in
  `results/reviewer/elasticsearch_table.tex` are similar to Table 6 in the paper. In particular, "Logical Queries" should be
  identical.

- (**C-D3** / **E-D3**) In
  `results/reviewer/reconstructions/opensearch-d10-r1_debruijn_greedy.txt`,
  verify that `contig_2` and `contig_6` match the contents of Figure 5. If
  interested, you can view other reconstructions from the |D| = 10 attacks in
  the same file, or view reconstructions from other attacks in
  `results/reviewer/reconstructions`.
