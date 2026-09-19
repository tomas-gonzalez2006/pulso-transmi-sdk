"""Run the local TFX pipeline for one immutable Pulso TransMi snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

import tfx
from tfx.components import CsvExampleGen, ExampleValidator, ImportSchemaGen, Pusher, SchemaGen, StatisticsGen, Trainer
from tfx.orchestration import metadata, pipeline
from tfx.orchestration.local.local_dag_runner import LocalDagRunner
from tfx.proto import example_gen_pb2, pusher_pb2, trainer_pb2


def build(snapshot_dir: Path, pipeline_root: Path, metadata_path: Path, serving_dir: Path) -> pipeline.Pipeline:
    input_config = example_gen_pb2.Input(splits=[
        example_gen_pb2.Input.Split(name="train", pattern="train.csv"),
        example_gen_pb2.Input.Split(name="eval", pattern="eval.csv"),
    ])
    example_gen = CsvExampleGen(input_base=str(snapshot_dir), input_config=input_config)
    statistics = StatisticsGen(examples=example_gen.outputs["examples"])
    schema = SchemaGen(statistics=statistics.outputs["statistics"])
    validator = ExampleValidator(statistics=statistics.outputs["statistics"], schema=schema.outputs["schema"])
    trainer = Trainer(
        module_file=str(Path(__file__).resolve().parents[1] / "src/pulso_transmi/tfx_trainer.py"),
        examples=example_gen.outputs["examples"],
        schema=schema.outputs["schema"],
        train_args=trainer_pb2.TrainArgs(num_steps=0),
        eval_args=trainer_pb2.EvalArgs(num_steps=0),
    )
    pusher = Pusher(model=trainer.outputs["model"], push_destination=pusher_pb2.PushDestination(filesystem=pusher_pb2.PushDestination.Filesystem(base_directory=str(serving_dir))))
    return pipeline.Pipeline(
        pipeline_name="pulso_transmi_tfx",
        pipeline_root=str(pipeline_root),
        components=[example_gen, statistics, schema, validator, trainer, pusher],
        enable_cache=True,
        metadata_connection_config=metadata.sqlite_metadata_connection_config(str(metadata_path)),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, default=Path("artifacts/tfx/pipeline"))
    parser.add_argument("--metadata", type=Path, default=Path("artifacts/tfx/metadata.db"))
    parser.add_argument("--serving-dir", type=Path, default=Path("artifacts/tfx/serving"))
    args = parser.parse_args()
    args.pipeline_root.mkdir(parents=True, exist_ok=True)
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.serving_dir.mkdir(parents=True, exist_ok=True)
    LocalDagRunner().run(build(args.snapshot_dir, args.pipeline_root, args.metadata, args.serving_dir))


if __name__ == "__main__":
    main()
