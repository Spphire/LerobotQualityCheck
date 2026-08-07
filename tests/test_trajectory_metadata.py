import unittest

import server


class TrajectoryMetadataTest(unittest.TestCase):
    def test_dataset_source_list_is_preferred_with_legacy_fallback(self):
        self.assertEqual(
            server.configured_dataset_sources({"dataset_paths": ["one", "two"]}),
            ["one", "two"],
        )
        self.assertEqual(
            server.configured_dataset_sources({"dataset_source": "legacy"}),
            ["legacy"],
        )

    def test_rollout_uses_teleop_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "rollout", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["world_up_axis"], "y")

    def test_unknown_device_keeps_identity_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "unknown", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "identity")


if __name__ == "__main__":
    unittest.main()
