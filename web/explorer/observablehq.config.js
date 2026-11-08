// Observable Framework config for the train-fusion explorer.
// Data loaders (src/data/[table].parquet.py, src/data/meta.json.py) are thin wrappers around
// train_fusion.explorer_data and only read what the pipeline already wrote to data/derived.
export default {
  title: "train-fusion explorer",
  root: "src",
  pages: [
    {name: "Reference segments", path: "/"},
    {name: "Trip vs reference", path: "/trip"},
    {name: "Timetable", path: "/timetable"}
  ],
  theme: ["air", "near-midnight"],
  // Python loaders need the project's environment (nix shell, or $PYTHON if set)
  interpreters: {".py": [process.env.PYTHON ?? "python3"]},
  footer: "",
  toc: false
};
