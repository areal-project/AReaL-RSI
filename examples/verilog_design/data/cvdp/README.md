---
license: other
pretty_name: CVDP Benchmark Dataset
configs:
- config_name: cvdp_nonagentic_code_generation_no_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
- config_name: cvdp_nonagentic_code_generation_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_nonagentic_code_generation_commercial.jsonl"
- config_name: cvdp_agentic_code_generation_no_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_agentic_code_generation_no_commercial.jsonl"
- config_name: cvdp_agentic_code_generation_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_agentic_code_generation_commercial.jsonl"
- config_name: cvdp_agentic_heavy_code_generation_no_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_agentic_heavy_code_generation_no_commercial.jsonl"
- config_name: cvdp_agentic_heavy_code_generation_commercial
  data_files:
  - split: eval
    path: "cvdp_v1.1.0_agentic_heavy_code_generation_commercial.jsonl"
#- config_name: cvdp_nonagentic_code_comprehension
#  data_files:
#  - split: eval
#    path: "cvdp_v1.1.0_nonagentic_code_comprehension.jsonl"
viewer_config:
  max_examples_per_split: 500  # default is 100; increase as needed
---

**Important please see "Files and versions" above for full list of files in the CVDP dataset.**

Please see [LICENSE](LICENSE) and [NOTICE](NOTICE) for licensing information. See [CHANGELOG](CHANGELOG) for changes.

This is the Comprehensive Verilog Design Problems (CVDP) benchmark dataset to use with the [CVDP infrastructure on GitHub](https://github.com/NVlabs/cvdp_benchmark).
