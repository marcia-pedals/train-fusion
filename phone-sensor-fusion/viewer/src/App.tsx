// The pages, chosen by the address: #/results (the evaluation's tables),
// #/ride/<id>?t0=&t1=&t= (one ride in detail, zoomed to [t0, t1], cursor at t) and
// #/dynamics (the train dynamics a run learned).
import {useHash} from "./lib/hash";
import DynamicsPage from "./dynamics/DynamicsPage";
import ResultsPage from "./results/ResultsPage";
import RidePage from "./ride/RidePage";

export default function App() {
  const {path} = useHash();
  if (path[0] === "ride" && path[1]) return <RidePage key={path[1]} id={path[1]} />;
  if (path[0] === "dynamics") return <DynamicsPage />;
  return <ResultsPage />;
}
