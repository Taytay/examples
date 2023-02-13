# Copyright 2022 MosaicML Examples authors
# SPDX-License-Identifier: Apache-2.0

"""A preliminary implementation of XSUM for fine-tuning."""

import logging
from typing import Any, Dict, Mapping, Optional, Union

import datasets
import transformers
from composer.core.evaluator import Evaluator
from composer.core.types import Dataset
from composer.utils import dist
# from omegaconf.listconfig import ListConfig
from torch.utils.data import DataLoader
from transformers import PreTrainedTokenizer, PreTrainedTokenizerFast

# from examples.ul2.src.summarization.metrics import RougeWithDetokenizer
from examples.ul2.src.utils import Seq2SeqFinetuningCollator

__all__ = ['build_xsum_dataloader']

log = logging.getLogger(__name__)


def build_xsum_dataloader(cfg: Mapping[str, Any],
                          device_batch_size: int,
                          mode: Optional[str] = None):
    """Builds a dataloader for training or evaluating SuperGLUE task(s)"""
    if cfg.get('name', 'xsum') != 'xsum':
        raise NameError(
            f'Tried to build xsum dataloader with cfg.name={cfg.name}')

    if mode not in ['train', 'eval']:
        raise ValueError(
            'When using multiple tasks, `mode` argument must be set to ' +\
            f'either `train` or `eval`, but got mode={mode}.'
        )

    tokenizer = transformers.AutoTokenizer.from_pretrained(
        cfg.dataset.tokenizer_name,
        model_max_length=cfg.dataset.max_seq_length)  #type: ignore (thirdparty)

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def _build_dataloader(dataset: Dataset,
                          batch_metadata: Optional[Dict[str, Any]] = None):
        return DataLoader(
            dataset,
            collate_fn=Seq2SeqFinetuningCollator(
                tokenizer,
                max_seq_length=cfg.dataset.max_seq_length,
                decoder_only_format=cfg.dataset.decoder_only_format,
                separator_text=cfg.dataset.get('separator_text'),
                batch_metadata=batch_metadata,
            ),
            batch_size=device_batch_size,
            sampler=dist.get_sampler(dataset,
                                     drop_last=cfg.drop_last,
                                     shuffle=cfg.shuffle),
            num_workers=cfg.num_workers,
            pin_memory=cfg.pin_memory,
            prefetch_factor=cfg.prefetch_factor,
            persistent_workers=cfg.persistent_workers,
            timeout=cfg.timeout,
        )

    dataset = create_xum_dataset(tokenizer,
                                 cfg.dataset.split,
                                 extra_prefix=cfg.dataset.get('extra_prefix'))

    if mode == 'train':
        return _build_dataloader(dataset)

    dataloader = _build_dataloader(dataset, {'generate_output': True})
    return Evaluator(label='xsum',
                     dataloader=dataloader,
                     metric_names=['RougeWithDetokenizer'])


def create_xum_dataset(
    tokenizer: Union[PreTrainedTokenizer, PreTrainedTokenizerFast],
    split: str,
    max_retries: int = 10,
    num_workers: int = 0,
    extra_prefix: Optional[str] = None,
    include_extra_example_info: bool = True,
):

    log.info(f'Loading XSUM on rank {dist.get_global_rank()}')
    download_config = datasets.DownloadConfig(max_retries=max_retries)
    dataset = datasets.load_dataset(
        'xsum',
        split=split,
        download_config=download_config,
    )

    def tokenize_function(inp: Mapping[str, Any]):
        """Format the text string and simply tokenize."""
        if extra_prefix is not None:
            prefix = f'{extra_prefix} '
        else:
            prefix = ''

        model_args = tokenizer(
            text=prefix + inp['document'],
            text_target=inp['summary'],
        )

        if include_extra_example_info:
            # Keep things like `idx` and `label`
            model_args.update(
                {k: v for k, v in inp.items() if not isinstance(v, str)})

        return model_args

    assert isinstance(dataset, datasets.Dataset)

    columns_to_remove = list(dataset[0].keys())

    safe_tok_name = tokenizer.name_or_path.replace('/', ',')
    fingerprint = f'xsum-{safe_tok_name}-tokenization-{split}'
    if include_extra_example_info:
        fingerprint += '-extras'

    dataset = dataset.map(
        tokenize_function,
        batched=False,
        num_proc=None if num_workers == 0 else num_workers,
        remove_columns=columns_to_remove,
        new_fingerprint=fingerprint,
        load_from_cache_file=True,
    )
    return dataset


# if __name__ == "__main__":
#     from omegaconf import OmegaConf as om
#     cfg = om.create(
#         {
#             "dataset": {
#                 "split": "validation",
#                 "tokenizer_name": 't5-base',
#                 "max_seq_length": 1024,
#                 "decoder_only_format": False,
#                 "separator_text": False,
#             },
#             "drop_last": False,
#             "shuffle": True,
#             "num_workers": 0,
#             "pin_memory": False,
#             "prefetch_factor": 2,
#             "persistent_workers": False,
#             "timeout": 0
#         }
#     )

#     tokenizer = transformers.AutoTokenizer.from_pretrained(cfg.dataset.tokenizer_name)

#     # dataloader = build_super_glue_task_dataloader(cfg, device_batch_size=2, mode='train')
#     dataloader = build_xsum_dataloader(cfg, device_batch_size=2, mode='eval')

#     # for evaluator in evaluators:
#     #     dataloader = evaluator.dataloader.dataloader
#     import torch
#     for i, batch in enumerate(dataloader):
#         print(f'-----Batch {i}-----')
#         for k, v in batch.items():
#             if isinstance(v, torch.Tensor):
#                 print(k, v.shape)
#             else:
#                 print(k, v)
#         for j in range(dataloader.batch_size):
#             print(f'--- Sample {j} ---')
#             if cfg.dataset.decoder_only_format:
#                 print('INPUT IDS:', tokenizer.decode(batch['input_ids'][j, batch['attention_mask'][j] == 1], skip_special_tokens=True))
#                 print('CONTEXT:  ', tokenizer.decode(batch['input_ids'][j, batch['bidirectional_mask'][j] == 1], skip_special_tokens=True))
#                 print('TARGET:   ', tokenizer.decode(batch['input_ids'][j, batch['labels'][j] != _HF_IGNORE_INDEX], skip_special_tokens=True))
#             else:
#                 print('CONTEXT:  ', tokenizer.decode(batch['input_ids'][j, batch['attention_mask'][j] == 1], skip_special_tokens=True))
#                 print('TARGET:   ', tokenizer.decode(batch['labels'][j, batch['decoder_attention_mask'][j] == 1], skip_special_tokens=True))
#         print('   ')
#         if i >= 5:
#             break
