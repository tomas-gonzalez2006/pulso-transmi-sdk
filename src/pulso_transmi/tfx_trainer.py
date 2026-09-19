"""TFX Trainer module for the Pulso TransMi occupancy model."""

from __future__ import annotations

import tensorflow as tf


FEATURES = (
    "station_code",
    "minute_sin",
    "minute_cos",
    "weekday_sin",
    "weekday_cos",
    "lag_1",
    "lag_4",
    "lag_96",
    "lag_672",
    "rain_mm",
    "rain_forecast",
    "temperature_c",
    "temperature_forecast",
    "event_intensity",
)


def _dataset(files: list[str], batch_size: int = 256, shuffle: bool = False) -> tf.data.Dataset:
    spec = {name: tf.io.FixedLenFeature([], tf.float32) for name in (*FEATURES, "demand")}
    dataset = tf.data.TFRecordDataset(files, compression_type="GZIP")
    dataset = dataset.map(lambda record: tf.io.parse_single_example(record, spec), num_parallel_calls=tf.data.AUTOTUNE)

    def split(example: dict[str, tf.Tensor]) -> tuple[dict[str, tf.Tensor], tf.Tensor]:
        label = example.pop("demand")
        return {name: tf.expand_dims(example[name], -1) for name in FEATURES}, label

    dataset = dataset.map(split, num_parallel_calls=tf.data.AUTOTUNE)
    if shuffle:
        dataset = dataset.shuffle(10_000, seed=42)
    return dataset.batch(batch_size).prefetch(tf.data.AUTOTUNE)


def _model() -> tf.keras.Model:
    inputs = {name: tf.keras.Input(shape=(1,), name=name, dtype=tf.float32) for name in FEATURES}
    values = tf.keras.layers.Concatenate()(list(inputs.values()))
    hidden = tf.keras.layers.Dense(128, activation="relu")(values)
    hidden = tf.keras.layers.Dropout(0.15)(hidden)
    hidden = tf.keras.layers.Dense(64, activation="relu")(hidden)
    output = tf.keras.layers.Dense(1, activation="relu", name="demand")(hidden)
    model = tf.keras.Model(inputs=inputs, outputs=output)
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3), loss="mae", metrics=[tf.keras.metrics.MeanAbsoluteError(name="mae")])
    return model


def run_fn(fn_args) -> None:
    model = _model()
    train = _dataset(fn_args.train_files, shuffle=True)
    evaluation = _dataset(fn_args.eval_files)
    model.fit(train, validation_data=evaluation, epochs=40, callbacks=[tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)])
    if hasattr(model, "export"):
        model.export(fn_args.serving_model_dir)
    else:
        model.save(fn_args.serving_model_dir, save_format="tf")
